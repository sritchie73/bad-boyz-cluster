from __future__ import division
import threading
import time 
import json
import copy
import shutil
import os
from datetime import datetime, timedelta

from urllib2 import HTTPError, URLError
from httplib import HTTPException

from gridservice.http import auth_header, JSONHTTPRequest
from gridservice.utils import validate_request
import gridservice.walltime as walltime

from gridservice.master.scheduler import RoundRobinScheduler, FCFSScheduler, DeadlineScheduler, DeadlineCostScheduler, PriorityQueueScheduler
from gridservice.master.job import Job

#
# The Grid.
#
# A digital fronteir.
# I tried to picture clusters of information as they moved through the computer.
# What did they look like? Ships? Motorcycles?
# Were the circuits like freeways?
# I kept dreaming of a world I'd never see.
#
# And then one day,
# I got in.
#

class Grid(object):
	
	NODE_TIMEOUT = 10

	SCHEDULERS = {
		'RoundRobin': RoundRobinScheduler,
		'FCFS': FCFSScheduler,
		'Deadline': DeadlineScheduler,
		'DeadlineCost': DeadlineCostScheduler,
		'PriorityQueue': PriorityQueueScheduler,
	}

	#
	# __init__(self, scheduler_func)
	#
	# Initialises The Grid using the given Scheduler
	#

	def __init__(self, username, password, scheduler):
		self.jobs = {}
		self.next_job_id = 0
		
		self.nodes = {}
		self.node_ids = {}

		self.auth_header = auth_header(username, password)

		# (Proportion of nodes, max wall_time (hours), list of nodes)
		self.node_queue = {
			'DEFAULT': (0.5, walltime.strptime("7:00:00:00"), []),
			'BATCH': (0.3, None, []),
			'FAST': (0.2, walltime.strptime("01:00:00"), []) 
		}

		self.next_node_id = 0

		self.queue_lock = threading.Lock()
		self.queue = []

		# Remove all job related files
		path = os.path.join('www', 'jobs')
		if os.path.exists(path):
			shutil.rmtree(path)

		# Start the scheduler
		self.scheduler = scheduler

	@property
	def scheduler(self):
		return self._scheduler

	@scheduler.setter
	def scheduler(self, scheduler):
		if scheduler in self.SCHEDULERS:
			scheduler_func = self.SCHEDULERS[scheduler]
		else:
			raise InvalidSchedulerException("Scheduler %s not found." % scheduler)
		
		if hasattr(self, '_scheduler'):
			self._scheduler.stop()

		self._scheduler = scheduler_func(self)
		self._scheduler.start()

	#
	# add_job(self, flags, wall_time, deadline, budget, job_type, name)
	#
	# Adds a new job to the Grid
	#
		
	def add_job(self, flags, wall_time, deadline, budget, job_type, name):
	
		# Need to check job_type is a valid queue
		if job_type is None:
			job_type = "DEFAULT"
		elif job_type not in self.node_queue.keys():
			raise InvalidJobTypeException(
				"Invalid Job Type specified: %s. Valid job types are: %s." % (job_type, ", ".join(self.node_queue.keys()))
				)
		
		# Check for Valid budget
		try:
			budget = int(budget)
		except (TypeError, ValueError):
			raise InvalidJobBudgetException("Invalid Budget specified: %s. Format: amount in cents as a whole number." % budget)
		if budget < 0:
			raise InvalidJobBudgetException("Invalid Budget specified: %s. Budget must be greater than 0" % budget)

		# Check that wall_time is valid:
		try:
			wall_stripped = walltime.strptime(wall_time)
		except WallTimeFormatException:
			raise InvalidWallTimeFormatException("Invalid Wall Time specified: %s. Format: DD:HH:MM:SS." % wall_time)

		# Check that deadline format is valid
		try:
			deadline_since_epoch = time.mktime(time.strptime(deadline, "%Y-%m-%d %H:%M:%S"))
		except ValueError:
			raise InvalidJobDeadlineFormatException("Invalid Deadline specified: %s. Format: YYYY-MM-DD HH:MM:SS" % deadline)

		# Check that deadline is valid
		if deadline_since_epoch <= int(time.time()):
			raise InvalidJobDeadlineException("Invalid Deadline specified: %s. Deadline specified is in the past." % deadline)
		
		# Check that deadline is reasonable
		if (deadline_since_epoch - walltime.wall_secs(wall_stripped)) < int(time.time()):
			raise InvalidJobDeadlineException(
				"Error: Current time plus wall time is later than the specified deadline. Please adjust either and resubmit."
				)
		
		# Check that wall time is within acceptable range for job queue placement
		if self.node_queue[job_type][1] != None and walltime.wall_secs(wall_stripped) > walltime.wall_secs(self.node_queue[job_type][1]):
			raise InvalidJobTypeException(
				"Invalid Job Type specified: %s. Wall time %s is too large. Wall time must be shorter than %s for job type %s."
				% (job_type, walltime.strftime(wall_stripped), self.node_queue[job_type][1], job_type)
				) 

		#
		# All tests passed, add to grid.
		#

		job = Job(
			job_id = self.next_job_id,
			flags = flags, 
			wall_time = wall_stripped, 
			deadline = deadline_since_epoch, 
			budget = budget,
			job_type = job_type,
			name = name
		)


		self.jobs[ self.next_job_id ] = job
		self.next_job_id += 1

		return job

	#
	# get_job(self, job_id)
	#
	# Gets a job based on job ID
	#

	def get_job(self, job_id):	

		if isinstance(job_id, str) and job_id.isdigit():
			job_id = int(job_id)

		if job_id in self.jobs:
			return self.jobs[ job_id ]
		else:
			raise JobNotFoundException("There is no job with id: %s" % job_id)

	#
	# kill_job(self, job)
	#
	# Kills a job, stops all running work units on all nodes
	#

	def kill_job(self, job):
		for unit in job.work_units:
			if unit.status == "RUNNING":
				try:
					node = self.nodes[ unit.node_id ]
					url = '%s/task/%s' % (self.get_node_url(node), unit.task_id)
					request = JSONHTTPRequest( 'DELETE', url, "", self.auth_header )
				except (HTTPException, URLError) as e:
					print "The node at %s is unavailable. Couldn't kill work unit." % self.get_node_url(node)

		job.kill()
	
	#
	# update_job_status(self, job_id, status)
	#
	# Updates a jobs status, currently only supports READY
	#

	def update_job_status(self, job_id, status):
		if status not in [ "READY" ]:
			raise InvalidJobStatusException("The job status %s is not valid." % status)

		job = self.get_job(job_id)
		
		if status == "READY":
			job.ready()
			self.add_to_queue(job)
		
		return job
		
	#
	# add_to_queue(self, job)
	#
	# Adds a job to the queue
	#

	def add_to_queue(self, job):
		with self.queue_lock:
			for work_unit in job.work_units:
				self.queue.append(work_unit)
	#
	# finish_work_unit(self, job, work_unit_id)
	#
	# Finishes the work unit and removes it from The Grid's internal
	# state of the Node it was running on.
	#

	def finish_work_unit(self, job, work_unit_id):
		unit = job.finish_work_unit(work_unit_id)
		node = self.nodes[ unit.node_id ]
		for key, work_unit in enumerate(node['work_units']):
			if work_unit == unit:
				del node['work_units'][ key ]

		return unit

	#
	# add_node(self, node)
	#
	# Takes a dict containing at minimum a host and port,
	# calculates a unique ID for the host/port if it hasn't
	# seen it before, and returns that ID
	#

	def add_node(self, node):
		node_ident = "%s:%s" % (node['host'], node['port'])

		if node_ident not in self.node_ids:
			node.update({'created_ts': int(time.time())})
			self.node_ids[ node_ident ] = self.next_node_id
			self.next_node_id += 1
		
		node.update({'came_online_ts': int(time.time()), 'heartbeat_ts': int(time.time())})

		node_id = self.get_node_id(node_ident)

		node['node_id'] = node_id
		node['status'] = "ONLINE"
		node['work_units'] = []
		node['type'] = self.get_node_type(node_id)
		node['node_ident']=node_ident

		self.add_to_node_queues(node_id, node['type'])

		self.nodes[ node_id ] = node

		return node_id

	#
	# get_node(self, node_id)
	#
	# Takes a node_is either as an internal id, or as 
	# the string HOST:PORT and returns the node as a dict
	#

	def get_node(self, node_id):

		if isinstance(node_id, str):
			if node_id.isdigit():
				node_id = int(node_id)
			else:
				node_id = self.get_node_id(node_id)

		if node_id in self.nodes:
			return self.nodes[ node_id ]
		else:
			raise NodeNotFoundException("There is no node with id: %s" % node_id)

	#
	# get_node_id(self, node_ident)
	#
	# Takes a node identifier in form of HOST:PORT and
	# returns the unique identifier of that node
	#

	def get_node_id(self, node_ident):
		if node_ident in self.node_ids:
			return self.node_ids[ node_ident ]
		else:
			raise NodeNotFoundException("There is no node with ident: %s" % node_ident)

	#
	# get_node_ident(self, node)
	#
	# A utility function for easily getting a node's ident
	#

	def get_node_ident(self, node):
		return "%s:%s" % (node['host'], node['port'])

	#
	# get_node_url(self, node)
	#
	# A utility function for easily getting a node's url
	#

	def get_node_url(self, node):
		return "http://%s" % (self.get_node_ident(node))

	#
	# get_node_type(self, node_id)
	# 
	# Get the type for a new node, determined by assigning the type that most
	# correctly load balances queues.
	#

	def get_node_type(self, node_id):
	    # First check remove the node_id from the queues if it exists already.
		# This will happen if a node is shut down and rebooted before The Grid
		# detects the node is dead from timing out.
		for key, (proportion, max_wall_time, nodes) in self.node_queue.items():
			if node_id in nodes:
				nodes.remove(node_id)

		total_nodes = len(self.node_ids)
		min_type = "NONE"
		min_dist = 1

		# Find the queue whos distance from its desired proportion is
		# minimized by adding the current node
		for key, (proportion, max_wall_time, nodes) in self.node_queue.items():
			new_proportion = (len(nodes) + 1)/total_nodes

			# Apply the l2-norm penalty to distance between proportions 
			# to penalise larger distances over shorter ones.
			dist = (abs(new_proportion - proportion))**(1/2)
			if dist < min_dist:
				min_dist = dist
				min_type = key
		return min_type
	
	#
	# add_to_node_queues(self, node_id, node_type):
	#
	# Adds a node to the specified queue
	#

	def add_to_node_queues(self, node_id, node_type):
		self.node_queue[node_type][2].append(node_id)

	#
	# remove_from_node_queues(self, node_id)
	#
	# Removes a node from the node_queues
	#

	def remove_from_node_queues(self, node_id):
		for key, (proportion, max_wall_time, nodes) in self.node_queue.items():
			if node_id in nodes:
				nodes.remove(node_id)
				break

	# 
	# update_node(self, node_id, update)
	#
	# Takes a node_id and a dict with an update
	# and updates the node dict with the given
	#

	def update_node(self, node_id, update):
		update.update({'heartbeat_ts': int(time.time())})

		self.get_node(node_id).update(update)
		return self.get_node(node_id)

	#
	# get_queued(self)
	#
	# A generator of all queued work units
	# 

	def get_queued(self):
		return [unit for unit in self.queue if unit.status == "QUEUED"]

	#
	# get_free_node
	#
	# A generator of node that have at least 1 core free.
	# If given a node_type will return only nodes of the specified type.
	# If there are no nodes registered with the specified type will return
	# a free DEFAULT node.
	#

	def get_free_node(self, node_type=None):

		self.remove_timed_out_nodes()
			
		# Get the list of nodes to check
		if node_type is None:
			node_list = self.nodes.values()
		elif node_type in self.node_queue.keys():
			node_ids = self.node_queue[node_type][2]

			# If there's no nodes in that queue assign to the DEFAULT queue
			if len(node_ids) is 0:
				node_ids = self.node_queue["DEFAULT"][2]

			# Recover nodes from node_ids list:
			node_list = []
			for node_id in node_ids:
				node_list.append(self.nodes[node_id])
		else:
			raise InvalidNodeTypeException("%s is not a valid priority queue type.\n" % node_type)

		# Check for node with at least 1 core free
		for node in node_list:
			if node['status'] == "ONLINE" and (node['cores'] - len(node['work_units']) > 0):
				yield node

	# 
	# remove_timed_out_nodes(self)
	#
	# Looks for nodes that have not had their heartbeat within
	# NODE_TIMEOUT and removes them from the nodes list and the 
	# different node queues.
	#
	
	def remove_timed_out_nodes(self):
		for node_id, node in list(self.nodes.items()):
			if node['status'] == "ONLINE" and node['heartbeat_ts'] + self.NODE_TIMEOUT < int(time.time()):
				print "Node %s has timed out." % (self.get_node_ident(node))

				# Remove the node by setting status to DEAD
				node['status'] = "DEAD"

				# Remove the node_id from the node queues
				self.remove_from_node_queues(node_id)

				# Requeue orphaned work units
				for unit in node['work_units']:

					if unit.status == "RUNNING":
						unit.reset()
						self.queue.append(unit)

	#
	# node_to_dict(self, node)
	#
	# For some stupid reason Nodes are still not an object,
	# but are a dict, so we need some fancy converting to
	# output a node nicely.
	#

	def node_to_dict(self, node):
		n = copy.copy(node)

		n['work_units'] = []
		for unit in node['work_units']:
			n['work_units'].append(unit.to_dict())

		return n


#
# InvalidSchedulerException
#

class InvalidSchedulerException(Exception):
	pass

#
# NodeNotFoundException
#

class NodeNotFoundException(Exception):
	pass

#
# InvalidNodeTypeException
#

class InvalidNodeTypeException(Exception):
	pass

#
# JobNotFoundException
#

class JobNotFoundException(Exception):
	pass


#
# InvalidJobParameterException
#
# Generic error class for job parameter exceptions
# when adding jobs from a client.
#

class InvalidJobParameterException(Exception):
	pass

#
# InvalidJobStatusException
#

class InvalidJobStatusException(InvalidJobParameterException):
	pass

#
# InvalidJobTypeException
#

class InvalidJobTypeException(InvalidJobParameterException):
	pass

#
# InvalidJobBudgetException
#

class InvalidJobBudgetException(InvalidJobParameterException):
	pass

#
# InvalidJobWallTimeFormatException
# 

class InvalidJobWallTimeFormatException(InvalidJobParameterException):
	pass

#
# InvalidJobDeadlineFormatException
# 

class InvalidJobDeadlineFormatException(InvalidJobParameterException):
	pass

#
# InvalidJobDeadlineException
#

class InvalidJobDeadlineException(InvalidJobParameterException):
	pass
