#!/usr/bin/env python

from paste import httpserver, reloader
import gridservice.utils
from gridservice import http
from gridservice.http import JSONRequest

def node_register_GET(_GET, _POST):
	response = JSONRequest( 'http://localhost:8051/node/register', 
		{ 
			'ip_address': 'localhost',
			'port': '8051',
			'cores': 1,
			'os': 'OSX'
		}
	)

	if response.get_reponse_code() == 200:
		return JSONResponse({ 'success': 'Node was registered with the Master' })
	else:
		return JSONResponse({ 'error_msg': 'Was unable to register with the master'})

routes = {
	('/node/register', 'GET'): node_register_GET,
}

if __name__ == '__main__':
	reloader.install()
	
	host = '127.0.0.1'
	port = 8050

	app = gridservice.utils.make_json_app(routes)
	httpserver.serve(app, host = host, port = port)