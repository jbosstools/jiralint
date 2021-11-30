import json
import requests

def jiraquery(options, url):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % options.jiratoken
    }
    
    if options.verbose:
        print("Query: " + options.jiraserver + url)
   
    response = requests.request("GET", options.jiraserver + url, headers=headers)

    if options.verbose:
        print(response.text)

    return response.json()

def jirapost(options, url, data):
    
    jdata = json.dumps(data)

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % options.jiratoken
    }
    
    if options.verbose:
        print("Post: " + options.jiraserver + url)
        print("Data: " + jdata)

    response = ''
    
    try: 
        response = requests.request(
            "POST",
            options.jiraserver + url,
            data=jdata,
            headers=headers
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print("Http Error:", e)
        raise(e)

    if options.verbose:
        print(response.text)

    return response.json()
    
def jiraupdate(options, url, data):

    jdata = json.dumps(data)
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % options.jiratoken
    }
    
    if options.verbose:
        print("Post: " + options.jiraserver + url)
        print("Data: " + jdata)

    response = ''
    
    try: 
        response = requests.request(
            "PUT",
            options.jiraserver + url,
            data=jdata,
            headers=headers
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print("Http Error:", e)
        raise(e)
    
    if options.verbose:
        print(response.text)

    return response.json()
