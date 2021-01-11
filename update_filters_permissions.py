from optparse import OptionParser
from common import shared
import urllib2
import re
import json
import sys
import os
import time
import datetime


usage = "usage: %prog -u <jirauser> -p <jirapwd> -f <filters.json>\nCreate/maintain set of filters defined in filters.json."

parser = OptionParser(usage)

#todo: move the shared options to common ?
parser.add_option("-u", "--user", dest="jirauser", help="jirauser")
parser.add_option("-p", "--pwd", dest="jirapwd", help="jirapwd")
parser.add_option("-s", "--server", dest="jiraserver", default="https://issues.redhat.com", help="Jira instance")
parser.add_option("-f", "--filters", dest="filterfiles", default="filters.json", help="comma separated list of filters to setup")
parser.add_option("-v", "--verbose", dest="verbose", action="store_true", help="more verbose logging")
(options, args) = parser.parse_args()
    
if not options.jirauser or not options.jirapwd:
    parser.error("Missing jirauser or jirapwd")


if options.filterfiles:
    #print "Force enabling global shared filters. Will not have any effect if user is not allowed to globally share objects in jira."
    #shared.jiraupdate(options, "/rest/api/latest/filter/defaultShareScope", { 'scope': 'GLOBAL' })

    allfilters = {}
    filterfiles = options.filterfiles.split(',')
    for filterfile in filterfiles:
        print "Processing filters found in " + filterfile
        filters = json.load(open(filterfile, 'r'))

        newfilters = filters.copy()
        for name, fields in filters.items():
            try:
                print "filter " + name
                data = [
                    {
                        'type': 'project',
                        'projectId': 10020
                    },
                    {
                        'type': 'project',
                        'projectId': 12310500
                    }
                ]
                
                if 'id' in fields:
                    print 'Checking filter ' + name
                    filter = shared.jiraquery(options, "/rest/api/latest/filter/" + fields['id'])
                    if len(filter['sharePermissions']) == 0:
                      print 'Updating filter ' + name
                      for permission in data:
                          shared.jirapost(options, "/rest/api/latest/filter/" + fields['id'] + "/permission", permission)
                else:
                    print 'Filter ' + name + ' already has some permissions'
            except urllib2.HTTPError, e:
                print "Problem with setting up filter %s" % (name);
