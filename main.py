import functions_framework
from google.cloud import firestore
from flask import request
from datetime import datetime
import requests
import json
import os
import re

ENV_VAR_MSG = "Specified environment variable is not set."

project_id = os.environ.get('GOOGLE_CLOUD_PROJECT', ENV_VAR_MSG)

db = firestore.Client(project=project_id)

@functions_framework.http
def handle_sierra_job(request):

    # Extract query parameters
    site_name = request.args.get('site_name')

    request_json = request.get_json(silent=True)
    lead_id = request_json.get('resourceList', [None])[0]
    communication_type = request_json.get('data', {}).get('communicationItemType')
    communication_id = request_json.get('data', {}).get('communicationItemId')
    agent_id = request_json.get('data', {}).get('adminUserId')

    if communication_type != 'PhoneCall':
        return "Not PhoneCall", 200

    #pulling config and validating if communication should be included
    client_config_ref = db.collection('clientConfigs').document(site_name)
    client_config_doc = client_config_ref.get()
    if not client_config_doc.exists:
        return f"Client Config not found for {site_name}", 200
    client_config = client_config_doc.to_dict()
    
    if client_config.get('excludeAgents', False):
        if not agent_id:
            return "Missing required data field AgentID", 200
        #exclude agents based on id
        if str(agent_id) not in client_config.get('allowedAdminUserIds', []):
            return f"Agent {agent_id} not allowed", 200
    
    if client_config.get('excludeViciLists', False):
        if not agent_id:
            return "Missing required data field AgentID", 200
        vici_list = client_config.get('adminUserIdToViciList', {}).get(str(agent_id), 0)
    else:
        vici_list = client_config.get('viciList', 0)

    if vici_list == 0:
        return f"Vici List not found for {site_name}", 200
    
    API_KEY = client_config.get('apiKey', '')
    if API_KEY == '':
        return f"API Key not found for {site_name}", 200

    headers = {
        "Content-Type": "application/json",
        "Sierra-ApiKey": API_KEY,
        "Sierra-OriginatingSystemName": "cf",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    # Get lead info from Sierra to fill required fields
    lead_info_ep = f"https://api.sierrainteractivedev.com/leads/get/{lead_id}"

    lead_details_response = requests.get(lead_info_ep, headers=headers)
    if not lead_details_response.ok:
        return f"Not Details - {lead_details_response.status_code}", 200
    lead_details_data = lead_details_response.json()
    lead_data = lead_details_data.get('data', {})

    call_info_ep = f"https://api.sierrainteractivedev.com/phoneCall/{communication_id}"
    call_info_response = requests.get(call_info_ep, headers=headers)
    if not call_info_response.ok:
        return f"Not Call Info - {call_info_response.status_code}", 200
    call_info_data = call_info_response.json()
    call_data = call_info_data.get('data', {})

    # Create a row in google spreadsheet
    headers = {
        "Content-Type": "application/json"
    }
    # Remove anything that is not a number and keep the length of the phone number to 10
    phone_number = re.sub(r'\D', '', lead_data.get('phone', ''))
    phone_number = (phone_number[:10] if len(phone_number) > 10 else phone_number.zfill(10))
    
    payload = {
        "FirstName": lead_data.get('firstName', ''),
        "LastName": lead_data.get('lastName', ''),
        "CallNotes": call_data.get('note', ''),
        "Email": lead_data.get('email', ''),
        "LeadID": lead_id,
        "SiteName": site_name,
        "ListID": vici_list,
        "Disposition": call_data.get('callStatus', ''),
        "Phone": phone_number,
        "Comments": call_data.get('callDuration', ''),
        "ViciDisp": call_data.get('callType', '')
    }
    response = requests.post(os.environ.get('SREADSHEET_URL', ENV_VAR_MSG), headers=headers, data=json.dumps(payload))
    if not response.ok:
        return f"Not Updated Spreadsheet - {response}", 200

    return "OK", 200
