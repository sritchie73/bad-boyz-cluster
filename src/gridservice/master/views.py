import os
import gridservice.utils
from gridservice import http
from gridservice.http import require_json, Response, FileResponse, JSONResponse
from gridservice.grid import Job

import gridservice.master.model as model

#
# job_POST
#
# Creates a new Job sent by a client and adds it 
# to the queue
#

@require_json
def job_POST(request):
	if gridservice.utils.validate_request(request.json, ['executable', 'files']):
		executable = request.json['executable']
		files = request.json['files']

		job = Job(executable, files)
		model.grid.thread.scheduler.add_to_queue(job)

		return JSONResponse({ 'success': "Job added successfully.", 'id': 1 }, 201)
	else:
		return JSONResponse({ 'error_msg': 'Invalid Job JSON received.' }, http.BAD_REQUEST)

#
# job_files_PUT(_GET, _POST, v)
# 
# Takes a binary PUT to a path and stores the file on 
# the local disk based on the id, type and path of the file
#

def job_files_PUT(request, v):
	request.raw_to_file(os.path.join("jobs", v['id'], "files", v['type'], v['path']))

	return JSONResponse(v)

#
# node_POST
#
# Takes a ip_address, port and cores and initiates
# the new node in the network.
#

@require_json
def node_POST(request):
	if gridservice.utils.validate_request(request.json, ['ip_address', 'port', 'cores', 'current_job', 'cpu']): 
		node = request.json

		model.grid.add_node(node)
		return JSONResponse({ 'success': "Node added successfully." }, 201)
	else:
		return JSONResponse({ 'error_msg': 'Invalid Node JSON received.' }, http.BAD_REQUEST)

#
# index_GET
#
# A nice alias for the console index
#

def index_GET(request):
	return FileResponse("console/console.html")

#
# file_GET
#
# Serves a file directly from disk
#

def file_GET(request, v):
	return FileResponse(v["file"])

#
# node_GET
#
# Who knows what this does yet?
#

def nodes_GET(request):
	nodeList = model.grid.nodes.values()
	return  JSONResponse({ 'success': "Job added successfully.", 'nodes': nodeList }, 200)
