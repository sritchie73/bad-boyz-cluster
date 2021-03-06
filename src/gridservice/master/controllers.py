import os
import time

import gridservice.utils
import gridservice.master.model as model

from gridservice import http
from gridservice.utils import validate_request
from gridservice.http import require_json, authenticate, FileResponse, JSONResponse
from gridservice.master.grid import NodeNotFoundException, JobNotFoundException, InvalidSchedulerException, InvalidJobParameterException
from gridservice.master.scheduler import NodeUnavailableException

#
# Authentication Decorators.
#

def auth_any(func):
	def decorator_func(*args, **kwargs):
		return authenticate(model.ADMINS + model.CLIENTS + model.NODES)(func)(*args, **kwargs)
	return decorator_func

def auth_admin(func):
	def decorator_func(*args, **kwargs):
		return authenticate(model.ADMINS)(func)(*args, **kwargs)
	return decorator_func

def auth_client(func):
	def decorator_func(*args, **kwargs):
		return authenticate(model.ADMINS + model.CLIENTS)(func)(*args, **kwargs)
	return decorator_func

def auth_node(func):
	def decorator_func(*args, **kwargs):
		return authenticate(model.ADMINS + model.NODES)(func)(*args, **kwargs)
	return decorator_func

#
# scheduler_PUT
#
# Sets the scheduler in use by The Grid
#

@require_json
@auth_admin
def scheduler_PUT(request):
	d = request.json

	if not validate_request(d, ['scheduler']):
		return JSONResponse({ 'error_msg': 'Invalid Scheduler JSON Received' }, http.BAD_REQUEST)
	
	try:
		model.grid.scheduler = request.json['scheduler']
	except InvalidSchedulerException as e:
		return JSONResponse({ 
			'error_msg': "Invalid Scheduler %s. Valid Schedulers: %s" %
			(request.json['scheduler'], ", ".join(model.grid.SCHEDULERS))
		}, 
		http.BAD_REQUEST)
	
	return JSONResponse({ 'success': 'Scheduler changed.' }, http.OK)

#
# job_GET(request)
#
# Returns a list of all jobs
#

@auth_client
def job_GET(request):	
	jobs = model.grid.jobs
	
	safe_jobs = {}
	for key, job in jobs.items():
		safe_jobs.update({ key: job.to_dict() })
	
	return JSONResponse(safe_jobs, http.OK)

#
# job_POST(request)
#
# Creates a new Job sent by a client 
#

@require_json
@auth_client
def job_POST(request):
	d = request.json
	if not validate_request(d, 
		['wall_time', 'deadline', 'flags', 'budget', 'job_type']):
		return JSONResponse({ 'error_msg': 'Invalid Job JSON received.' }, http.BAD_REQUEST)
	try:
		name = "Unknown"
		if d.has_key("name"):
			name = d["name"]

		job = model.grid.add_job(
			flags = d['flags'], 
			wall_time = d['wall_time'], 
			deadline = d['deadline'], 
			budget = d['budget'],
			job_type = d['job_type'],
			name = name
		)
	except InvalidJobParameterException as e:
		return JSONResponse({ 'error_msg': "%s" % e.args[0] }, http.BAD_REQUEST)

	return JSONResponse({ 'success': "Job added successfully.", 'id': job.job_id }, http.OK)

#
# job_id_GET(request, v)
#
# Get a job by the id in the URI
#

@auth_client
def job_id_GET(request, v):
	try:
		job = model.grid.get_job(v['id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)
	
	return JSONResponse(job.to_dict(), http.OK)

#
# job_id_DELETE(request, v)
#
# Kills the job running with ID
#

@auth_client
def job_id_DELETE(request, v):
	try:
		job = model.grid.get_job(v['id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)

	job.kill_msg = "Killed on request by client."
	try:
		model.grid.kill_job(job)
	except NodeUnavailableException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.BAD_REQUEST)

	return JSONResponse({ 'success': "Job killed successfully." }, http.OK)

#
# job_status_PUT(request, v)
#
# Sets the status of the job by the id in the URI
#

@require_json
@auth_client
def job_status_PUT(request, v):
	d = request.json

	if not validate_request(d, ['status']): 
		return JSONResponse({ 'error_msg': 'Invalid status JSON received.' }, http.BAD_REQUEST)

	try:
		job = model.grid.update_job_status(v['id'], d['status'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)
	except InvalidJobParameterException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.BAD_REQUEST)

	return JSONResponse(job.to_dict(), http.OK)

#
# job_output_files_GET(request, v)
# 
# Returns a list of filenames of a job with finished work units.
# Additional info_msg is returned if the job is still running, 
# has been killed, or has killed components, warning that the 
# content/number of output files will be incomplete.
#

@auth_client
def job_output_files_GET(request, v):
	try:
		job = model.grid.get_job(v['id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)

	if job.status == "READY":
		return JSONResponse({ 'error_msg': "Job %s is still waiting to be scheduled." % v['id']}, http.BAD_REQUEST)
	elif job.status == "PENDING":
		return JSONResponse({ 'error_msg': "The Grid is still initialising Job %s." % v['id']}, http.BAD_REQUEST)
	elif job.status == "RUNNING":
		info_msg = "Warning: Job %s is still running. Output files for running portions of the job will be missing." % v['id']
	elif job.status == "KILLED":
		info_msg = "Warning: Job %s has been killed: %s Output returned will be incomplete." % (v['id'], job.kill_msg)
	else:
		info_msg = ""

	files_list = []
	for unit in job.work_units:
		if unit.status == "FINISHED" or unit.status == "KILLED":
			files_list.append("%s_%s.o" % (job.job_id, unit.work_unit_id))
			files_list.append("%s_%s.e" % (job.job_id, unit.work_unit_id))
		if unit.status == "KILLED" and job.status != "KILLED":
			info_msg += "Warning: Work unit %s has been killed:" % unit.work_unit_id
			info_msg += " %s Output returned will be incomplete for this work unit.\n" % unit.kill_msg

	return JSONResponse({ 'output_URIs': files_list, 'info_msg': info_msg }, http.OK)

#
# job_output_file_GET(request, v)
# 
# Returns a FileResponse of the file at the given URI
#

@auth_client
def job_output_file_GET(request, v):
	try:
		job = model.grid.get_job(v['id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)

	file_path = "%s/%s" % (job.output_dir, v['file_name'])

	try:
		f = open(file_path, "r")
		f.close()
	except IOError as e:
		return JSONResponse({ 'error_msg': "Unable to open file %s for Job %s; File does not exist" 
								% (v['file_name'], v['id'])}, http.BAD_REQUEST)

	return FileResponse(file_path)

#
# job_files_GET(request, v)
# 
# Returns a FileResponse of the file at the given URI
#

@auth_node
def job_files_GET(request, v):
	try:
		job = model.grid.get_job(v['id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)

	if v['type'] == "files":
		file_path = job.input_path(v['path'])
	elif v['type'] == "output":
		file_path = job.output_path(v['path'])
	elif v['type'] == "executable":
		file_path = job.executable_path(v['path'])
	else:
		return JSONResponse({ 'error_msg': "Invalid file type." }, http.BAD_REQUEST)

	return FileResponse(file_path)

#
# job_files_PUT(request, v)
# 
# Takes a binary PUT to a path and stores the file on 
# the local disk based on the id and path of the file
#

@auth_any
def job_files_PUT(request, v):
	try:
		job = model.grid.get_job(v['id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)

	if v['type'] == "files":
		file_path = job.input_path(v['path'])
	elif v['type'] == "output":
		file_path = job.output_path(v['path'])
	elif v['type'] == "executable":
		file_path = job.executable_path(v['path'])
	else:
		return JSONResponse({ 'error_msg': "Invalid file type." }, http.BAD_REQUEST)

	job.create_file_path(file_path)
	request.raw_to_file(file_path)
	
	if v['type'] == "executable":
		job.add_executable(v['path'])
	else:
		job.add_file(v['path'])
	
	return JSONResponse(v)

#
# job_workunit_POST(request, v)
# 
# Marks the given workunit as finished or killed.
#

@require_json
@auth_node
def job_workunit_POST(request, v):
	if not validate_request(request.json, ['work_unit_id', 'kill_msg']): 
		return JSONResponse({ 'error_msg': 'Invalid Work Unit JSON received.' }, http.BAD_REQUEST)

	try:
		job = model.grid.get_job(v['id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)

	unit = model.grid.finish_work_unit(job, request.json['work_unit_id'])
	
	if request.json['kill_msg'] != None:
		unit.kill_msg = request.json['kill_msg']
		unit.kill()

	return JSONResponse(unit.to_dict(), http.OK)

#
# node_GET(request)
#
# Get a list of all nodes
#

@auth_client
def node_GET(request):
	nodes = model.grid.nodes

	safe_nodes = {}
	for key, node in nodes.items():
		safe_nodes.update({ key: model.grid.node_to_dict(node) })
	
	return JSONResponse(safe_nodes, http.OK)

#
# node_POST(request)
#
# Takes a ip_address, port and cores and initiates
# the new node in the network.
#

@require_json
@auth_node
def node_POST(request):
	if not validate_request(request.json, ['host', 'port', 'cores', 'programs', 'cost']): 
		return JSONResponse({ 'error_msg': 'Invalid Node JSON received.' }, http.BAD_REQUEST)
	
	node = request.json
	node_id = model.grid.add_node(node)

	return JSONResponse({ 'node_id': node_id }, http.OK)

#
# node_id_GET(request, v)
#
# Returns the node at the given URI
#

@auth_node
def node_id_GET(request, v):
	try:
		node = model.grid.get_node(v['id'])
	except NodeNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)

	return JSONResponse(model.grid.node_to_dict(node), http.OK)

#
# node_id_POST(request, v)
#
# Updates the node at the given URI, returns the node
#

@require_json
@auth_node
def node_id_POST(request, v):
	if not validate_request(request.json, []): 
		return JSONResponse({ 'error_msg': 'Invalid Node JSON received.' }, http.BAD_REQUEST)

	try:
		node = model.grid.update_node(v['id'], request.json)
	except NodeNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)

	return JSONResponse(model.grid.node_to_dict(node), http.OK)

#
# Routes related to console function
#

#
# index_GET(request)
#
# A nice alias for the console index
#

@auth_client
def index_GET(request):
	return FileResponse(os.path.join("www", "console/console.html"))

#
# file_GET(request, v)
#
# Serves a file directly from disk
#

@auth_client
def file_GET(request, v):
	return FileResponse(os.path.join("www", v["file"]))


# 
# Duplicate of nodes_GET
#
# Returns a list isntead of a dictionary.
#

@auth_client
def nodes_GET(request):
	nodeList = model.grid.nodes.values()

	jsonNodes = []
	for node in nodeList:
		try:
			cpu = node['cpu']
		except KeyError:
			cpu = 0
		if  node['status'] != "DEAD":
			n = {
				"host": node['host'],
				"port": node['port'],
				"node_id": node['node_id'],
				"status": node['status'],
				"work_units": [],
				"type": node['type'],
				"node_ident": node['node_ident'],
				"cores": node['cores'],
				"cpu": cpu,
				"cost": node['cost']
			}
			for unit in node["work_units"]:
				n["work_units"].append(unit.to_dict())
			
			jsonNodes.append(n)

	return  JSONResponse({ 'nodes': jsonNodes}, 200)

#
# Duplicate of job_GET
#
# Returns a list of jobs on The Grid.
#

@auth_client
def jobs_GET(request):
	queued_jobs = model.grid.get_queued()
	queued_jobs = model.grid.jobs

	ljobs=[]
	for j in queued_jobs.values():
		ljobs.append(j.to_dict())


	return  JSONResponse({ 'jobs': ljobs}, 200)


#
# log_GET(request)
#
# Returns the updated portion of the Scheduler Log since
# the last call to log_GET
#

@auth_client
def log_GET(request):

	# We also want to track an "id" so that we can make UI updates
	# more efficiently (so we don't redraw stuff thats already drawn)
	start = len(model.grid.scheduler.mem_log)-100
	if start < 0:
		start=0

	logs=[]
	for i in xrange(start, len(model.grid.scheduler.mem_log)):
		logs.append( { "id": i, "log": model.grid.scheduler.mem_log[i]})

	return  JSONResponse({ 'log': logs}, 200)

#
# Duplicate of job_files_PUT
#

@auth_client
def job_submit_file_POST(request, v):
	file_name = request.query["qqfile"][0]
	try:
		job = model.grid.get_job(v['tmp_job_id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)


	file_path = job.input_path(file_name)

	job.create_file_path(file_path)
	request.raw_to_file(file_path)

	job.add_file(file_name)

	return JSONResponse( {'tmp_job_id': v['tmp_job_id'], 'filename': file_path} , 200)

#
# Duplicate of job_files_PUT
#

@auth_client
def job_submit_executable_POST(request, v):
	file_name = request.query["qqfile"][0]
	try:
		job = model.grid.get_job(v['tmp_job_id'])
	except JobNotFoundException as e:
		return JSONResponse({ 'error_msg': e.args[0] }, http.NOT_FOUND)


	file_path = job.executable_path(file_name)
	
	job.create_file_path(file_path)
	request.raw_to_file(file_path)

	job.add_executable(file_name)

	return JSONResponse( {'tmp_job_id': v['tmp_job_id'], 'filename': file_path} , 200)
