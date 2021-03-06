#!/usr/bin/env python

# Client sends sleep(3) to be distributed across three processes three times
# Good for demonstrating RoundRobin

import os
import time

from optparse import OptionParser

parser = OptionParser(usage="./RoundRobin.py --gh HOSTNAME --gp PORT -t JOB_TYPE -s SCHEDULER")

parser.add_option("--gh", "--grid_hostname", dest="ghost",
	help="The hostname the client should listen on",
	metavar="HOSTNAME", default="127.0.0.1")

parser.add_option("--gp", "--grid_port", dest="gport",
	help="The port the client should listen on",
	metavar="PORT", default = 8051)

parser.add_option("-t", "--job_type", dest="job_type",
	help="The type of the jobs",
	metavar="JOB_TYPE", default="DEFAULT")

parser.add_option("-s", "--scheduler", dest="scheduler",
	help="The scheduler The Grid should use.",
	metavar="SCHEDULER", default="RoundRobin")

(options, args) = parser.parse_args()

if options.scheduler != "NOCHANGE":
	os.system(
		"./client.py --gh %s --gp %s --username admin --password admin -s %s" % (options.ghost, options.gport, options.scheduler)
		)


os.system(
	"./client.py --gh %s --gp %s -e test.py -t %s -b 500 testfiles/f3.txt testfiles/f3.txt testfiles/f3.txt"
	% (options.ghost, options.gport, options.job_type)
	)

os.system(
	"./client.py --gh %s --gp %s -e test.py -t %s -b 500 testfiles/f3.txt testfiles/f3.txt testfiles/f3.txt"
	% (options.ghost, options.gport, options.job_type)
	)

os.system(
	"./client.py --gh %s --gp %s -e test.py -t %s -b 500 testfiles/f3.txt testfiles/f3.txt testfiles/f3.txt"
	% (options.ghost, options.gport, options.job_type)
	)

# Demonstrates Addition of jobs later.
time.sleep(3)

os.system(
	"./client.py --gh %s --gp %s -e test.py -t %s -b 500 testfiles/f3.txt testfiles/f3.txt testfiles/f3.txt"
	% (options.ghost, options.gport, options.job_type)
	)
