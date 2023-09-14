import bugzilla
from jira.client import JIRA, JIRAError
from optparse import OptionParser
import urllib, sys, os
import pprint
from common import shared
import pickle
import re
from datetime import datetime
from datetime import timedelta
import time
import pytz
import sys
from collections import defaultdict
import logging

######################################################
## Jenkins usage:
# 
## Python is in /usr/bin/python2.7 or /usr/bin/python but not on all slaves
# DOES NOT RUN with Python 3.5
# whereis python && python -V
# if [[ ! -w ${HOME}/.local/bin ]]; then pipFolder=${HOME}/.local/bin; else pipFolder=${WORKSPACE}/.local/bin; fi
# mkdir -p ${pipFolder}
# if [[ ! -x ${pipFolder}/pip ]]; then  # get pip
#  curl https://bootstrap.pypa.io/get-pip.py > get-pip.py
#  python -W ignore get-pip.py --user
# fi
# pip install --upgrade --user pip bugzillatools python-bugzilla jira pytz pbr funcsigs
# 
######################################################
#
### To see list of installed pip modules and versions:
#
# pip freeze 
#
#######################################################

httpdebug = False

NO_VERSION = "!_NO_VERSION_!"

def parse_options():
    usage = "Usage: %prog -u <jirauser> -p <jirapwd> \nCreates proxy issues for bugzilla issues in jira"

    parser = OptionParser(usage)
    parser.add_option("-u", "--user", dest="jirauser", help="jirauser")
    parser.add_option("-p", "--pwd", dest="jirapwd", help="jirapwd")
    parser.add_option("-k", "--jiratoken", dest="jiratoken", help="JIRA Token")
    parser.add_option("-s", "--server", dest="jiraserver", default="https://issues.stage.jboss.org", help="Jira instance")
    parser.add_option("-B", "--bzserver", dest="bzserver", default="https://bugs.eclipse.org/bugs/", help="BZ instance, eg., https://bugs.eclipse.org/bugs/ or https://bugzilla.redhat.com/")
    parser.add_option("-I", "--issue-prefix", dest="issue_prefix", default="EBZ", help="prefix to use on issue links: RHBZ, EBZ, etc.")
    parser.add_option("-J", "--jira-project", dest="jira_project", default="ERT", help="project in JIRA: RHBZ, ERT, etc.")
    parser.add_option("-d", "--dry-run", dest="dryrun", action="store_true", help="run without creating proxy issues")
    parser.add_option("-v", "--verbose", dest="verbose", action="store_true", help="more verbose console output")
    parser.add_option("-a", "--auto-create", dest="autocreate", action="store_true", help="if set, automatically create components and versions as needed")
    parser.add_option("-A", "--auto-accept", dest="autoaccept", action="store_true", help="if set, automatically accept created issues")
    parser.add_option("-m", "--min-age", dest="minimum_age_to_process", help="if set, query only bugzillas changed in the last x hours")
    parser.add_option("-S", "--start-date", dest="start_date", default="", help="if set, show only bugzillas changed since start date (yyyy-mm-dd)")
    parser.add_option("-C", "--color", dest="colorconsole", action="store_true", help="if set, show colours in console with bash escapes")
    parser.add_option("-H", "--html-color", dest="htmlcolorconsole", action="store_true", help="if set, show colours in console with html")

    parser.add_option("-E", "--eclipse-bugzilla", dest="eclipse_bugzilla_defaults", action="store_true", help="shortcut for Eclipse Bugzilla defaults")
    parser.add_option("-R", "--redhat-bugzilla", dest="redhat_bugzilla_defaults", action="store_true", help="shortcut for Red Hat Bugzilla defaults")

    (options, args) = parser.parse_args()

    if options.eclipse_bugzilla_defaults:
        options.bzserver="https://bugs.eclipse.org/bugs/"
        options.issue_prefix="EBZ"
        options.jira_project="ERT"

    if options.redhat_bugzilla_defaults:
        options.bzserver="https://bugzilla.redhat.com/"
        options.issue_prefix="RHBZ"
        options.jira_project="RHBZ"

    if (not options.jirauser or not options.jirapwd) and "userpass" in os.environ:
        # check if os.environ["userpass"] is set and use that if defined
        #sys.exit("Got os.environ[userpass] = " + os.environ["userpass"])
        userpass_bits = os.environ["userpass"].split(":")
        options.jirauser = userpass_bits[0]
        options.jirapwd = userpass_bits[1]

    if ((not options.jirauser or not options.jirapwd) and not options.jiratoken):
        parser.error("Missing jirauser/jirapwd or jiratoken")

    return options

components = []
versions = []

## failure data
missing_versions = defaultdict(set)
jira_failure = defaultdict(set)

### Enables http debugging
if httpdebug:
    import requests
    import httplib
    httplib.HTTPConnection.debuglevel = 1
    logging.basicConfig() # you need to initialize logging, otherwise you will not see anything from requests
    logging.getLogger().setLevel(logging.DEBUG)
    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(logging.DEBUG)
    requests_log.propagate = True

pp = pprint.PrettyPrinter() 

transitionmap = {
   ("Open", None, "Open", None) : None, # its already correct
   ("Open", None, "Resolved", "Done") : {"id" : "5", "resolution" : "Done" }, 
   ("Open", None, "Closed", "Cannot Reproduce Bug") : {"id" : "3", "resolution" : "Cannot Reproduce Bug" },
   ("Open", None, "Resolved", "Duplicate Issue") : {"id" : "2", "resolution" : "Duplicate Issue"},
   ("Open", None, "Reopened", None) : None, # can't go to reopen without closing so just leaving it in open
   ("Open", None, "Coding In Progress", None) : {"id" : "4"}

   }
    
def lookup_proxy(options, bug):
    #TODO should keep a local cache from BZ->JIRA to avoid constant querying
    payload = {'jql': 'project = ' + options.jira_project + ' and summary ~ \'' + options.issue_prefix + '#' + str(bug.id) +'\'', 'maxResults' : 5}
    data = shared.jiraquery(options, "/rest/api/latest/search?" + urllib.urlencode(payload))
    count = len(data['issues'])
    if (count == 0):
        return 
    elif (count == 1):
        return data['issues'][0]
    else:
        print "[WARNING] Multiple issues found for " + str(bug.id)
        print data['issues']
        return 

# check if remote link exists, eg., is https://issues.redhat.com/rest/api/latest/issue/ERT-356/remotelink == [] or contains actual content?
def lookup_remotelink(options, jira_id):
    data = shared.jiraquery(options, "/rest/api/latest/issue/" + jira_id + "/remotelink")
    if (data and len(data) >=1):
        # print "[INFO] Found remotelink for " + jira
        return data[0]
    else:
        return

# set up issue link to options.issue_prefix = RHBZ / EBZ
def create_remotelink(jira_id, bug):
    link_dict = { "title": options.issue_prefix + " #" + str(bug.id), "url": bug.weburl }
    if (options.verbose):
        print "[DEBUG] Add remotelink: " + str(link_dict)
    jira.add_simple_link(jira_id, object=link_dict)

## just test the mapping but don't create anything
def create_proxy_jira_dict_test(options, bug):
    jiraversion  = bz_to_jira_version(options, bug)

## Create the jira dict object for how the bugzilla *should* look like
def create_proxy_jira_dict(options, bug):
    global versions
    jiraversion  = bz_to_jira_version(options, bug)
    fixversion=[]

    ## check version exists, if not don't create proxy jira.
    if (not next((v for v in versions if jiraversion == v.name), None)):
        if (jiraversion and jiraversion != NO_VERSION): 
            if (options.autocreate):
                accept = "Y"
            else:
                accept = raw_input("Create " + jiraversion + " ?")
            if accept.capitalize() in "Y":
                print norm + "[WARNING] Version '" + green + jiraversion + norm + "' mapped from '" + \
                    green + bug.target_milestone + norm + "' not found in " + green + options.jira_project + \
                    norm + ". Please create it or fix the mapping. " + blue + "Bug: " + str(bug) + norm
                newv = jira.create_version(jiraversion, options.jira_project)
                versions = jira.project_versions(options.jira_project)
                jiraversion = newv.name
            else:
                print red + "[ERROR] Version '" + green + jiraversion + norm + "' mapped from '" + \
                    green + bug.target_milestone + red + "' not found in " + green + options.jira_project + \
                    red + ". Please create it or fix the mapping. " + blue + "Bug: " + str(bug) + norm
                missing_versions[jiraversion].add(bug)
                return
            
        if (not jiraversion):
            print red + "[ERROR] No mapping for '" + green + bug.target_milestone + red \
                + "'. Please fix the mapping. " + blue + "Bug: " + str(bug) + norm 
            jiraversion = "Missing Map"
            return
            
    ## TODO make this logic more clear.
    ## for now we have the same test twice to avoid None to fall through.
    if (jiraversion and jiraversion != NO_VERSION): 
        fixversion=[{ "name" : jiraversion }]

    ## ensure the product name exists as a component
    global components
    if (not next((c for c in components if bug.product == c.name), None)):
        if (options.autocreate):
            accept = "Y"
        else:
            accept = raw_input("Create component: " + bug.product + " ?")
                
        if accept.capitalize() in "Y":
            comp = jira.create_component(bug.product, options.jira_project)
            components = jira.project_components(options.jira_project)


    labels=['bzira']
    labels.append(bug.component)
    if (bug.target_milestone and bug.target_milestone!="---"):
        labels.append(bug.target_milestone.replace(" ", "_")) # label not allowed to have spaces.

    issue_dict = {
        'project' : { 'key': options.jira_project },
        'summary' : bug.summary + ' [' + options.issue_prefix + '#' + str(bug.id) + "]",
        'description' : bug.getcomments()[0]['text'], # TODO this loads all comments...everytime. probably should wait to do this once it is absolutely needed.
        'issuetype' : { 'name' : 'Task' }, # No notion of types in bugzilla just taking the most generic/non-specifc in jira
        'priority' : { 'name' : bz_to_jira_priority(options, bug) },
        'labels' :   labels,
        'fixVersions' : fixversion,
        'components' : [{ "name" : bug.product }],
    }

    return issue_dict

def map_thym(version):
    if re.match(r"2.0.0", version):
        return re.sub(r"2.0.0", r"Neon (4.6)", version)
    elif re.match(r"2.([123]).0", version):
        return re.sub(r"2.([123]).0", r"Neon.\1 (4.6)", version)
    else:
        return NO_VERSION

def map_tycho(version):
    if re.match(r"1.3.0", version):
        return re.sub(r"1.3.0", r"2018-09", version)
    elif re.match(r"1.2.0", version):
        return re.sub(r"1.2.0", r"Photon (4.8)", version)
    else:
        return NO_VERSION

def map_linuxtools(version):
    if re.match(r"7.2.*", version):
        return re.sub(r"7.2.*", r"2018-12", version)
    if re.match(r"7.1.*", version):
        return re.sub(r"7.1.*", r"2018-09", version)
    elif re.match(r"7.0.*", version):
        return re.sub(r"7.0.*", r"Photon (4.8)", version)

    elif re.match(r"6.0.0", version):
        return re.sub(r"6.0.0", r"Oxygen.3 (4.7)", version)
    elif re.match(r"6.([123]).0", version):
        return re.sub(r"6.([123]).0", r"Oxygen.3 (4.7)", version)

    else:
        return NO_VERSION

def map_platform(version):
    if re.match(r"4.10([.1-9]*)", version):
        return re.sub(r"4.10([.1-9]*)", r"2018-12", version)

    elif re.match(r"4.9([.1-9]*)", version):
        return re.sub(r"4.9([.1-9]*)", r"2018-09", version)

    elif re.match(r"4.8\.([123])", version):
        return re.sub(r"4.8\.([123])", r"Photon (4.8)", version)
    elif re.match(r"4.8 (.*)", version):
        return re.sub(r"4.8 (.*)", r"Photon (4.8)", version)
    elif re.match(r"4.8", version):
        return re.sub(r"4.8", r"Photon (4.8)", version)
    elif re.match(r"4.7\.([123])", version):
        return re.sub(r"4.7\.([123])", r"Oxygen.3 (4.7)", version)
    elif re.match(r"4.7 (.*)", version):
        return re.sub(r"4.7 (.*)", r"Oxygen.3 (4.7) \1", version)

    else:
        return NO_VERSION

def map_webtools(version):
    if re.match(r"3.12([.1-9]*)", version):
        return re.sub(r"3.12([.1-9]*)", r"2018-12", version)

    elif re.match(r"3.11([.1-9]*)", version):
        return re.sub(r"3.11([.1-9]*)", r"2018-09", version)

    elif re.match(r"3.10\.([0-9])", version):
        return re.sub(r"3.10\.([0-9])", r"Photon (4.8)", version)
    elif re.match(r"3.10 (.*)", version):
        return re.sub(r"3.10 (.*)", r"Photon (4.8)", version)
    elif re.match(r"3.10", version):
        return re.sub(r"3.10", r"Photon (4.8)", version)

    elif re.match(r"3.9\.([0-9])", version):
        return re.sub(r"3.9\.([0-9])", r"Oxygen.3 (4.7)", version)
    elif re.match(r"3.9 (.*)", version):
        return re.sub(r"3.9 (.*)", r"Oxygen.3 (4.7) \1", version)
    elif re.match(r"3.9", version):
        return re.sub(r"3.9", r"Oxygen.3 (4.7)", version)

    else:
        return NO_VERSION

def map_m2e(version):

    # is this correct ???
    if re.match(r"1.9.1", version):
        return re.sub(r"1.9.1", r"2018-09", version)

    elif re.match(r"1.9.0", version):
        return re.sub(r"1.9.0", r"Photon (4.8)", version)

    elif re.match(r"1.8(.*)/Oxygen (.*)", version):
        return re.sub(r"1.8(.*)/Oxygen (.*)", r"Oxygen.3 (4.7) \2", version)

    else:
        return NO_VERSION

def map_orbit(version):
    if re.match(r"Photon([.1234]*) (.+)", version):
        return re.sub(r"Photon([.1234]*) (.+)", r"Photon (4.8)", version)
    elif re.match(r"Oxygen([.1234]*) (.+)", version):
        return re.sub(r"Oxygen([.1234]*) (.+)", r"Oxygen.3 (4.7)", version)
    else:
        return NO_VERSION

# Eclipse Marketplace Client (MPC)
def map_mpc(version):
    if re.match(r"1.7\.(.*)", version):
        return re.sub(r"1.7(.*)",     r"Photon (4.8)", version)

    elif re.match(r"1.6\.(.*)", version):
        return re.sub(r"1.6(.*)",     r"Oxygen.3 (4.7)", version)

    else:
        return NO_VERSION

################################

def map_fedora(version):
    if re.match(r"([0-9]+|rawhide)", version):
        return re.sub(r"([0-9]+|rawhide)", r"Fedora \1", version)
def map_devtools(version):
	# is this correct?
    if re.match(r"rh-eclipse410", version):
        return re.sub(r"rh-eclipse410", r"2018-12", version)
    elif re.match(r"rh-eclipse49", version):
        return re.sub(r"rh-eclipse49", r"2018-09", version)
    elif re.match(r"rh-eclipse48", version):
        return re.sub(r"rh-eclipse48", r"Photon (4.8)", version)
    elif re.match(r"rh-eclipse47", version):
        return re.sub(r"rh-eclipse47", r"Oxygen.3 (4.7)", version)
    else:
        return NO_VERSION

bzprod_version_map = {
    #"WTP Incubator" : (lambda version: NO_VERSION), // no obvious mapping available for the Target Milestones
    "JSDT" : map_webtools,
    "WTP Source Editing" : map_webtools,
    "WTP Common Tools" : map_webtools,
    "WTP ServerTools" : map_webtools,
    "WTP Webservices" : map_webtools,
    "Platform" : map_platform,
    "Linux Tools" : map_linuxtools,
    "MPC" : map_mpc,
    "m2e" : map_m2e,
    "Thym" : map_thym,
    "Tycho" : map_tycho,
    "Orbit" : map_orbit,

    "DevTools" : map_devtools,
    "Fedora" : map_fedora,
    "Errata Tool" : NO_VERSION,
    "Red Hat Customer Portal" : NO_VERSION
    }
    
def bz_to_jira_version(options, bug):
    """Return corresponding jira version for bug version. None means mapping not known. NO_VERSION means it has no fixversion."""
    bzversion = bug.target_milestone
    b2j = None

    ## '---' corresponds to no version set.
    if (bzversion == "---"):
        return NO_VERSION

    ## Use jira version Future for versions that is tied to no specific version
    if (bzversion == 'Future'):
        return 'Future'
    
    if bug.product in bzprod_version_map:
        b2j = bzprod_version_map[bug.product]
        jiraversion = b2j(bzversion)
        if (jiraversion):
            #if (options.verbose):
            print "[INFO] " + "Map: " + yellow + bug.product + norm + " / " + yellow + bzversion + norm + " -> " + green + str(jiraversion) + norm
            return jiraversion
        else:
            print red + "[ERROR] " + " Unknown version for " + yellow + bug.product + red + " / " + yellow + bzversion + norm
    else:
        print red + "[ERROR] " + " No version mapper found for " + yellow + bug.product + norm

bz2jira_priority = {
     'blocker' : 'Blocker',
     'critical' : 'Critical',
     'major' : 'Major',
     'normal' : 'Major',
     'minor' : 'Minor',
     'trivial' : 'Trivial',
     'enhancement' : 'Trivial' #TODO determine if 'enhancement' is really an indicator of a feature request, or simply a priority/complexity flag
    }
    
def bz_to_jira_priority(options, bug):
    if bug.severity is not None and bug.severity != 'unspecified':
        return bz2jira_priority[bug.severity] # Jira is dumb. jira priority is severity.
    else:
        return bz2jira_priority['major']

bz2jira_status = {
           "NEW" : "Open",
           "REOPENED": "Reopened",
           "RESOLVED" : "Resolved",
           "VERIFIED" : "Verified",
           "CLOSED" : "Closed",
           "ASSIGNED" : "Coding In Progress", # TODO determine if this is the right approximation
    }
    
def bz_to_jira_status(options, bug):
    jstatusid = None

    if bug.status in bz2jira_status:
        jstatus = bz2jira_status[bug.status]
        jstatusid = next((s for s in statuses if jstatus == s.name), None)

    if (jstatusid):
        return jstatusid

    raise ValueError('Could not find matching status for ' + bug.status)
    
bz2jira_resolution = {
           "FIXED": "Done",
           "INVALID" : "Invalid",
           "WONTFIX" : "Won't Fix",
           "DUPLICATE" : "Duplicate Issue",
           "WORKSFORME" : "Cannot Reproduce Bug",
           "MOVED" : "Migrated to another ITS",
           "NOT_UPSTREAM" : "Invalid" # don't have an exact mapping so using invalid as "best approximation"
    }
    
def bz_to_jira_resolution(options, bug):
    jstatusid = None

    if (bug.resolution == ""):
        return None
    
    if bug.resolution in bz2jira_resolution:
        jresolution = bz2jira_resolution[bug.resolution]
        jresolutionid = next((s for s in resolutions if jresolution == s.name), None)
    elif bug.resolution == "":
        jresolution = "None"
        jresolutionid = next((s for s in resolutions if jresolution == s.name), None)
               
    if (jresolutionid):
        return jresolutionid

    raise ValueError('Could not find matching resolution for ' + bug.resolution)

def process(bug, bugs):
    newissue = None

    changeddate = datetime.strptime(str(bug.delta_ts), '%Y%m%dT%H:%M:%S')
    difference = now - changeddate

    if (options.verbose):
        print ""
        print '[DEBUG] %s - %s [%s, %s, [%s]] {%s} (%s) -> ' % (bug.id, bug.summary, bug.product, bug.component, bug.target_milestone, bug.delta_ts, difference) + yellow + bzserver + "show_bug.cgi?id=" + str(bug.id) + norm
    else:
        sys.stdout.write('.')
        
    issue_dict = create_proxy_jira_dict(options, bug)

    if (issue_dict):
        proxyissue = lookup_proxy(options, bug)
        
        if (proxyissue):
            if (options.verbose):
                print "[INFO] " + yellow + bzserver + "show_bug.cgi?id=" + str(bug.id) + norm + " already proxied as " + blue + options.jiraserver + "/browse/" + proxyissue['key']  + norm + "; checking if something needs updating/syncing."

            fields = {}
            if (not next((c for c in proxyissue['fields']['components'] if bug.product == c['name']), None)):
                #TODO this check for existence in list of components
                # but then overwrites anything else. Problematic or not ?
                updcomponents = [{"name" : bug.product}]
                fields["components"] = updcomponents

                #TODO see if fixversions matches, see if status/resolution matches?
                
                if len(fields)>0:
                    print "Updating " + proxyissue['key'] + " with " + str(fields)
                    isbug = jira.issue(proxyissue['key'])
                    isbug.update(fields)
                else:
                    print "No detected changes."

            # check if there's an existing remotelink; if not, add one
            remotelink = lookup_remotelink(options, proxyissue['key'])
            if (not remotelink):
                create_remotelink(proxyissue['key'],bug)
        else:
            if (options.dryrun):
                print "[INFO] Want to create jira for " + str(bug)
            else:
                print "[INFO] Creating jira for " + str(bug)
            if (options.verbose):
                print "[DEBUG] " + str(issue_dict)

            if (options.dryrun):
                return
            
            newissue = jira.create_issue(fields=issue_dict)
            bugs.append(newissue)
            print "[INFO] Created " + green + options.jiraserver + "/browse/" + newissue.key + norm

            # Setup issue link to options.issue_prefix = RHBZ / EBZ
            create_remotelink(newissue.key,bug)
            # Check for transition needed
            jstatus = bz_to_jira_status(options, bug)
            jresolution = bz_to_jira_resolution(options, bug)

            #print ""
            #print "Need to transitiation from " + str(newissue.fields.status) + "/" + str(newissue.fields.resolution) +" to " + str(jstatus.name) + "/" + (str(jresolution.name) if jresolution else '(nothing)')

            transid = (
                 newissue.fields.status.name if newissue.fields.status else None,
                 newissue.fields.resolution.name if newissue.fields.resolution else None,
                 jstatus.name if jstatus else None,
                 jresolution.name if jresolution else None)

            if (transid in transitionmap):
                trans = transitionmap[transid]
                if (trans):
                    #print "Want to do " + str(transid) + " with " + str(trans)
                    #print "Can do: " + str(jira.transitions(newissue))

                    wantedres={ "name": trans["resolution"] } if "resolution" in trans else None
                    #print "Wanted res: " + str(wantedres)

                    try:
                        if (wantedres):
                            jira.transition_issue(newissue, trans["id"],resolution=wantedres)
                        else:
                            jira.transition_issue(newissue, trans["id"])
                    except JIRAError as je:
                        print je
                        jira_failure[newissue.key].add("Could not perform transition" + str(trans) + " error: " + str(je))
                #else:
                    #print "No transition needed"
            else:
                raise ValueError("Do not know how to do transition for " + str(transid))

    return newissue

options = parse_options()

# override defaults if set
if (options.bzserver):
    bzserver=options.bzserver
    if not bzserver.endswith("/"):
        bzserver=bzserver+"/"
basequery = bzserver + "buglist.cgi?status_whiteboard=RHT"

if (options.colorconsole):
    # colours for console
    norm="\033[0;39m"
    green="\033[1;32m"
    red="\033[1;31m"
    blue="\033[1;34m"
    purple="\033[0;35m"
    yellow="\033[0;33m"
elif (options.htmlcolorconsole):
    norm="</b>"
    green="<b style='color:green'>"
    red="<b style='color:red'>"
    blue="<b style='color:blue'>"
    purple="<b style='color:purple'>"
    yellow="<b style='color:orange'>"
else:
    norm=""
    green=""
    red=""
    blue=""
    purple=""
    yellow=""

# TODO cache results locally so we don't have to keep hitting live server to do iterations

# get current datetime in UTC for comparison to bug.delta_ts, which is also in UTC; use this diff to ignore processing old bugzillas
now = datetime.utcnow()
if (options.verbose):
    print "[DEBUG] " + "Current datetime: " + yellow + str(now) + " (UTC)" + norm
    print "" 

# calculate relative date if options.start_date not provided but minimum_age_to_process is provided
if (options.start_date):
    last_change_time = datetime.strptime(str(options.start_date),'%Y-%m-%d')
elif (options.minimum_age_to_process):
    last_change_time = now - timedelta(hours=int(options.minimum_age_to_process))
else:
    last_change_time = None
    
# to query only 3hrs of recent changes:
# https://bugzilla.redhat.com/buglist.cgi?chfieldfrom=3h&status_whiteboard=RHT&order=changeddate%20DESC%2C or
# https://bugs.eclipse.org/bugs/buglist.cgi?chfieldfrom=3h&status_whiteboard=RHT&order=changeddate%20DESC%2C
# but since chfieldfrom not supported in xmlrpc, use last_change_time instead with specific date, not relative one
if (last_change_time):
    query = basequery + "&last_change_time=" + last_change_time.strftime('%Y-%m-%d+%H:%M')
else:
    query = basequery
    
bz = bugzilla.Bugzilla(url=bzserver + "xmlrpc.cgi")

queryobj = bz.url_to_query(query)

print "[DEBUG] xmlrpc post: " + purple + str(queryobj) + norm
# print equivalent web url since xmlrpc.cgi uses last_change_time, but buglist.cgi queries use chfieldfrom
print "[DEBUG] buglist get: " + purple + query.replace("last_change_time","chfieldfrom") + norm
    
issues = bz.query(queryobj)

print "[DEBUG] " + "Found " + yellow + str(len(issues)) + norm + " bugzillas to process"

if (len(issues) > 0):

    print "[INFO] " + "Logging in to " + purple + options.jiraserver + norm
    jira = JIRA(options={'server':options.jiraserver}, basic_auth=(options.jirauser, options.jirapwd))

    #TODO should get these data into something more structured than individual global variables.
    versions = jira.project_versions(options.jira_project)
    components = jira.project_components(options.jira_project)

    if (options.verbose):
        print "[DEBUG] " + "Found " + yellow + str(len(components)) + norm + " components and " + yellow + str(len(versions)) + norm + " versions in JIRA"

    resolutions = jira.resolutions()
    statuses = jira.statuses()

    createdbugs = []

    for bug in issues:
        try:
            process(bug, createdbugs)
        except ValueError as ve:
            print red + "[ERROR] Issue when processing " + blue + str(bug) + red + ". Cannot determine if the bug was created or not. See details above. " + norm
            print ve


    ## report issues
    for v,k in missing_versions.iteritems():
        print "Missing version '" + v + "'"
        for b in k:
            print "  " + b.product + ": " + b.weburl
        
    for v,k in jira_failure.iteritems():
        print "Jira " + v + " gave following errors:"
        for b in k:
            print "  " + b
            
    # Prompt user to accept new JIRAs or delete them
    if (len(createdbugs)>0 and not options.autoaccept):
        accept = raw_input("Accept " + str(len(createdbugs)) + " created JIRAs? [Y/n] ")
        if accept.capitalize() in ["N"]:
            for b in createdbugs:
                print "[INFO] " + "Delete " + red + options.jiraserver + "/browse/" + str(b) + norm
                b.delete()
    else:
        print "[INFO] " + str(len(createdbugs)) + " JIRAs created (from " + str(len(issues)) + " issues) [since " + last_change_time.strftime('%Y-%m-%d+%H:%M') + "]"
else:
    print "[INFO] No bugzillas found matching the query. Nothing to do."
