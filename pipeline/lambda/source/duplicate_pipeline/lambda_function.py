import json
import boto3
import logging
import os

logger = logging.getLogger()
# Change this for the verbosity of the log
logger.setLevel(logging.DEBUG)

prod_pipeline_name = os.environ['prodPipeline']
dev_pipeline_name = os.environ['devPipeline']
qa_pipeline_name = os.environ['qaPipeline']

master_branch_name = os.environ['masterBranchName']
develop_branch_name = os.environ['developBranchName']
release_branch_name = os.environ['releaseBranchName']
feature_branch_name = os.environ['featureBranchName']
hotfix_branch_name = os.environ['hotfixBranchName']


EVENT_ACTIONS_BRANCH=["referenceCreated","referenceUpdated","referenceDeleted"]
EVENT_ACTIONS_PR=["pullRequestCreated","pullRequestMergeStatusUpdated","pullRequestStatusChanged","pullRequestSourceBranchUpdated"]

BRANCH_NAMES=[ master_branch_name,develop_branch_name,release_branch_name,feature_branch_name,hotfix_branch_name ]
BRANCH_PIPELINE_MAPPING = { master_branch_name : prod_pipeline_name, develop_branch_name: dev_pipeline_name, release_branch_name: qa_pipeline_name  }
BRANCH_EXCLUDE=[ master_branch_name, develop_branch_name, release_branch_name ]

codecommit_client = boto3.client('codecommit')
codepipeline_client = boto3.client('codepipeline')

def find_pipeline(destination_branch):
    pipeline_name = None
    try:
        pipeline_name=BRANCH_PIPELINE_MAPPING.get(destination_branch)
    except KeyError as err:
        logger.error('Selecting pipeline name from mapping failed. Branch: %s. Error: %s', destination_branch, err)
    except Exception as err:
        logger.error(' Error: %s', err)

    return pipeline_name

def pipeline_exist(pipeline_name):
    status = False
    try:        
        codepipeline_client.get_pipeline(name=pipeline_name)
        status = True
    except codepipeline_client.exceptions.PipelineNotFoundException as err:
        logger.info('Pipeline %s not found. Error: %s', pipeline_name, err)
        status = False
    except Exception as err:
        logger.error(' Error: %s', err)

    return status

def clone_pipeline(clone_source_name, clone_target_name, source_branch): 
    result = { 'statusCode': 500, 'message': 'Unknown Error' }

    pipeline_branch_name = "branch_name_error"

    for i,value in enumerate(source_branch):
        if i == 0:
            pipeline_branch_name = str(value)
        else:
            pipeline_branch_name += "/" + str(value)        
    
    try:        
        response = codepipeline_client.get_pipeline(name=clone_source_name)
    except codepipeline_client.exceptions.PipelineNotFoundException as err:
        logger.error('Source pipeline %s not found for cloning. Error: %s', clone_source_name, err)    
        result['statusCode'] = 500
        result['message'] = "Source pipeline could not be found"
    except Exception as err:
        logger.error(' Error: %s', err)
        result['statusCode'] = 500
        result['message'] = err

    try:
        pipeline_source = response.get("pipeline")
    except KeyError as err:
        logger.error('Selecting source pipeline %s failed. Error: %s', clone_source_name, err)
        result['statusCode'] = 500
        result['message'] = "Selecting source pipeline failed"
    except Exception as err:
        logger.error(' Error: %s', err)
        result['statusCode'] = 500
        result['message'] = err
    
    try:
        logger.debug('Start creating clone pipeline %s', clone_target_name)
        pipeline_clone ={
            "artifactStore": pipeline_source.get("artifactStore"),
            "name": clone_target_name,
            "roleArn": pipeline_source.get("roleArn"),
            "stages": pipeline_source.get("stages"),
            "version":1 
        }
        
        pipeline_clone["stages"][0]["actions"][0]["configuration"]["BranchName"] = pipeline_branch_name        
        response = codepipeline_client.create_pipeline(pipeline=pipeline_clone)

        if response.get("ResponseMetadata").get("HTTPStatusCode") == 200:
            logger.info('Pipeline %s is created.', clone_target_name)
            result['statusCode'] = response.get("ResponseMetadata").get("HTTPStatusCode")
            result['message'] = "Pipeline creation successfully"
        else:
            logger.warn('Pipeline %s could not be created. Response: %s', clone_target_name, response)
            result['statusCode'] = response.get("ResponseMetadata").get("HTTPStatusCode")
            result['message'] = "Pipeline could not be created"


    except Exception as err:
        logger.error(' Error: %s', err)
        result['statusCode'] = 500
        result['message'] = err
    
    return result


def destroy_pipeline(target): 
    result = { 'statusCode': 500, 'message': 'Unknown Error' }

    try:        
        response = codepipeline_client.delete_pipeline(name=target)
        if response.get("ResponseMetadata").get("HTTPStatusCode") == 200:
            logger.debug('Pipeline %s deleted.', target)
            result['statusCode'] = 200
            result['message'] = "Pipeline deletion successfully"
        else:
            logger.warn('Pipeline %s could not be deleted. Response: %s', target, response)
            result['statusCode'] = 500
            result['message'] = "Pipelineould not be deleted"
        
    except codepipeline_client.exceptions.PipelineNotFoundException as err:
        logger.error('Pipeline %s not found. Error: %s', target, err)
        result['statusCode'] = 400
        result['message'] = "Pipeline not found"                
    except Exception as err:
        logger.error(' Error: %s', err)
        result['statusCode'] = 500
        result['message'] = err
    
    return result

def find_branch_name(raw_input):
    # TODO: add branch not found 
    result = []
    try: 
        split_input=str(raw_input).split("/")
        length=len(split_input)-1
        for i,value in enumerate(split_input):
            if value in BRANCH_NAMES:
                result.append(value)                
                logger.debug('branch name %s found in %s', value, raw_input)  
                if i < length:
                    logger.debug('branch %s has a subvalue in %s', value, raw_input)                    
                    result.append(split_input[i+1])                    

    except IndexError as err:
        logger.warn('Selecting subvalue from %s failed. Error: %s', raw_input, err)
    except Exception as err:
        logger.error(' Error: %s', err)

    return result

    

def event_source_handler(event_source, event):

    result = { 'statusCode': 400, 'message': 'Source handler not found' }

    try:
        event_action = event["detail"]["event"]
        logger.debug('Handling event action %s', event_action)
        if event_action in EVENT_ACTIONS_BRANCH:
            # TODO: Add check for referenceType == branch
            reference_type = event["detail"]["referenceType"]    
            branch = find_branch_name(event["detail"]["referenceName"])
            logger.debug('Event %s in %s %s', event_action, reference_type, branch)
            
            if len(branch) > 1:
                branch_name = branch[0]
                branch_subname = branch[1]
            else:
                branch_name = branch[0]
                branch_subname = None

            if not ((branch_name in BRANCH_EXCLUDE) and (branch_subname is None )):
                pipeline_clone_source = find_pipeline(branch_name)
                pipeline_clone_name = pipeline_clone_source + "-" + branch_name + "-" + branch_subname
                if len(pipeline_clone_name) > 100:
                    size = len(pipeline_clone_name) - 105
                    logging.warn('Pipeline name %s is too long, shorten it by the first %s chars.', pipeline_clone_name, size)
                    pipeline_clone_name = pipeline_clone_name[15:]

                if event_action == "referenceCreated":
                    if not pipeline_exist(pipeline_clone_name):
                        logging.debug('Pipeline %s does not exist. Creating a new pipeline clone from %s!', pipeline_clone_name, pipeline_clone_source)
                        result = clone_pipeline(pipeline_clone_source, pipeline_clone_name, branch)
                    else:
                        logging.debug('Pipeline %s already exists!', pipeline_clone_name)
                        result['statusCode'] = 300
                        result['message'] = "Pipeline already exists"  
                elif event_action == "referenceDeleted":
                    if pipeline_exist(pipeline_clone_name):
                        logging.debug('Deleting the existing pipeline %s for %s !', pipeline_clone_name, branch)
                        result = destroy_pipeline(pipeline_clone_name)
                    else:
                        logging.warn('Deleting the pipeline %s failed because it does not exist!', pipeline_clone_name)
                        result['statusCode'] = 400
                        result['message'] = "Pipeline not found"  
            else:
                logging.info('For branch %s without subvalue will no pipeline be created!', branch_name)
                result['statusCode'] = 301
                result['message'] = "Pipeline not created"                 

        elif event_action in EVENT_ACTIONS_PR:
            destination_branch = find_branch_name(event["detail"]["destinationReference"])
            source_branch = find_branch_name(event["detail"]["sourceReference"])
            logger.debug('Event %s in %s for target %s', event_action, source_branch, destination_branch)
            
            pr_status = event["detail"]["pullRequestStatus"]
            pr_number = event["detail"]["pullRequestId"]

            pipeline_clone_source = find_pipeline(destination_branch[0])
            pipeline_clone_name = pipeline_clone_source + "-pr-" + pr_number 
            if len(pipeline_clone_name) > 100:
                size = len(pipeline_clone_name) - 105
                logging.warn('Pipeline name %s is too long, shorten it by the first %s chars.', pipeline_clone_name, size)
                pipeline_clone_name = pipeline_clone_name[15:]

            logging.debug('PullRequest %s has status %s.', pr_number, pr_status)
            
            if pr_status == "Open":
                if not pipeline_exist(pipeline_clone_name):
                    logging.debug('Pipeline %s does not exist. Creating a new pipeline clone from %s!', pipeline_clone_name, pipeline_clone_source)
                    result = clone_pipeline(pipeline_clone_source, pipeline_clone_name, source_branch)                                   
                else:
                    logging.debug('Pipeline %s already exists!', pipeline_clone_name)
                    result['statusCode'] = 300
                    result['message'] = "Pipeline already exists"                      
            elif pr_status == "Closed":
                if pipeline_exist(pipeline_clone_name):
                    logging.debug('Deleting the existing pipeline %s for %s PullRequest %s!', pipeline_clone_name, pr_status, pr_number)                
                    result = destroy_pipeline(pipeline_clone_name)
                else:
                    logging.warn('Deleting the pipeline %s failed because it does not exist!', pipeline_clone_name)
                    result['statusCode'] = 400
                    result['message'] = "Pipeline not found"

                                
    except Exception as err:
        logger.error('Error: %s', err)
        result['statusCode'] = 500
        result['message'] = err

    return result    


def lambda_handler(event, context):
    
    try:
        event_source = event["source"]
        if event_source == "aws.codecommit":
            logger.debug('Handling event for %s event source', event_source)
            result = event_source_handler(event_source, event)
        else:
            logger.info('No handler registered for the %s event source', event_source)
    except Exception as err:
        logger.error(' Error: %s', err)
    
    return {
        'statusCode': result['statusCode'],
        'body': result['message']
    }






