# Introduction

This creates a CI/CD ecosystem with 3 environments in AWS. It is build with CodePipeline, CodeBuild, TaskCat and Lambda. Designed for testing and deploying AWS CloudFormation templates but could easily be customized for other usecases. Used to work with Git Flow and CodeCommit, but can be modified to use as source GitHub or Bitbucket. 


__What will be created?__

- One (or more) CodeCommit repository
- Three CodePipelines with a Source, Test, Deploy und Cleanup stage
- A SNS topic for notifications
- CloudWatch Event rules for
   - creation and deletion of branches
   - PullRequest events
   - failed pipeline executions 
   - successful run of the stage "Deploy"
- Multiple S3 buckets (some **public**) 
- A lambda function which clones and deletes pipelines for specific branches and pullrequests 

__What is provided?__

- CloudFormation template to deploy the CI/CD ressources and a lambda function
- Demo CloudFormation template which deploys a nested stack 
   - the stack creates a S3 hosted webpage for demo purpose

# Design

## Source stage

In the source stage is the source repository and branch defined. When a commit is recognized, the pipeline starts and fetch the source. In the moment `PollForSourceChanges` is used, but will be changed to CloudWatch Events in the future.

## Testing stage

> by default all tests are deployed in us-east-1, you can change this in the taskcat config file `.taskcat.yml` 

To test the templates taskcat is started in a CodeBuild project. Taskcat is a tool that runs different tests on AWS CloudFormation templates. Released and continuously improved by the amazing aws-quickstart team (https://github.com/aws-quickstart/taskcat).

The test build process is described in the `buildspec.yml` which ca be found in the provided demo CloudFormation stack. The taskcat conifgurations are stored for every stage in the `ci/$stage/` folder.

The outputs of taskcat are copied to a common S3 bucket. When the tests have been successful, the CloudFormations templates are synced to the pipeline artifacts bucket, so they can be used for the deployment proccess (needed to deploy nested stacks).


## Deployment stage

When the taskcat tests have been successful, the deployment stage is started. The first action will create a Cloudformation ChangeSet (if the not stack exists, the state will be `REVIEW_IN_PROGRESS`). In the pipeline for the `prod` environment is a manual approval for the deploy implemented, this will be send to the SNS topic and waits for a approve or decline.
As next step is ChangeSet executed and the stack will be updated. If the deployment is successful, a CloudWatch Event will trigger a message to the SNS topic.

As template source is the `main.cf.yml`  used and to configure the template `template-configuration.json`. Both are provided in the output artifact from the test stage (TaskCatArtifacts), which is used as input artifact in this stage. As parameter override is the name and region of the pipeline artifacts bucket provided and also a combined folder name (`cloudformation/$stage`). These values are used for the `TemplateUrl` of the nested stack. I found no other solution to deploy nested stacks in a ActionTypeId CloudFormation.

The parameters precedence is `ParameterOverrides > template-configuration.json > Default`.

## Cleanup stage

In the cleanup stage are the templates files cleared, which have been temporarily stored in the pipeline artifacts bucket. For this demo usecase there is also a copy of a file `index.html`, to the created website S3 bucket. The name of the environment is dynamically inserted into the file.

___

## Lambda function `duplicate_pipeline`

CodePipeline has no buildin support for wildcards as branch names. That means, if you want to have for example a QA pipeline which deploys your published versions from release/* (e.g. release/app_v1.01). You need to setup a pipeline for each release or change the source in your QA pipeline every time you release a version. Also there is no feature to deploy pull requests to pipelines.

This functions is used to solve this missing features. Every time when a matching CodeCommit event is detected by the CloudWatch Events rules, the Lambda function is triggerd.

When a event is procesed, the function checks if the event action is in `EVENT_ACTIONS_BRANCH` or `EVENT_ACTIONS_PR` defined. Depending on the type of event, the process is slightly different (due the many duplicate codelines, I assume that a smarter handling is most likely possible). 

You can use `BRANCH_PIPELINE_MAPPING` to map the branch names to pipelines that are used as clone source.
 
### Branch events

If it is a reference/branch event action, it checks if the name is in the exclude list and if the sliced substring (`release/app_v1.00 -> ['release','app_v1.00'] vs. release -> ['release']`)  is empty to prevent the creation of a duplicate "source" pipeline.  

When the event action is a created branch, the function checks if a pipeline already exist. If not it creates one with a new concatenation name and the new branch as source.

On a delete event, the pipeline is deleted.


### PullRequest events

For a PullRequest the handling is similar, it checks for the source and target branch, look up the source pipeline for the clone and creates a new name. 

If the pullrequest has the status `Open` , it checks if there is already a existing pipline for this pull request. Depending on that, it creates one or skips this step.

On a PullRequest event action with the status `Closed`, the pipeline is deleted.

### Trigger

The following CloudWatch Events pattern are used as rules to trigger the Lambda function.

```
detail-type: CodeCommit Pull Request State Change
source: aws.codecommit
-----
detail-type:CodeCommit Repository State Change
source: aws.codecommit
referenceType: "branch
```                 

### Environment Variables

In the Lambda function are environment variables used, to provide the source pipeline and the branch names. These are dynamically filled in the CloudFormation deployment of the Lambda function.

```
prod_pipeline_name = os.environ['prodPipeline']
dev_pipeline_name = os.environ['devPipeline']
qa_pipeline_name = os.environ['qaPipeline']

master_branch_name = os.environ['masterBranchName']
develop_branch_name = os.environ['developBranchName']
release_branch_name = os.environ['releaseBranchName']
feature_branch_name = os.environ['featureBranchName']
hotfix_branch_name = os.environ['hotfixBranchName']
```



# Setup

<Warning>

- Deploying this setup in your AWS account will cause cost for infrastructure! 
- The S3 buckets created by the demo stack are public readable!
- After creating the stack the pipelines could start, but will fail because the repository is empty / the branches don't exist!

</Warning>

## Step 1

The easiest way to deploy the pipeline template is to use the AWS CLI and a S3 bucket, where the files will be uploaded to. Alternatively you can upload the files manually to a bucket and deploy the template with the AWS managemnent console.

__Create a S3 bucket__

Create a S3 bucket where the pipeline CloudFormation templates will be stored.

```
aws s3 s3://mybucket
```
## Step 2

__Deploy base infrastructure__

Package the CloudFormation templates

```
aws cloudformation package --template-file templates/main.cf.json  --s3-bucket mybucket --s3-prefix cicd-pipeline --output-template-file templates/packaged-main.cf.json
```

Copy the zipped Lambda to the S3 Bucket

```
aws s3 cp lambda/zip/duplicate_pipeline/lambda.zip   s3://mybucket/cicd-pipeline/lambda/zip/duplicate_pipeline/
```

Deploy the CloudFormation templates 
 
- Please change the email for the SNS topic subscription and the bucket name!

```
aws cloudformation deploy  --template-file templates/packaged-main.cf.json --stack-name  pipeline-demo --s3-bucket mybucket --s3-prefix cicd-pipeline  --capabilities CAPABILITY_NAMED_IAM --parameter-overrides "SubscriberMail=dummy@example.org" "S3BucketDeployment=mybucket" "S3BucketFolder=cicd-pipeline"
```
As soon as the mail arrives, you can accept the SNS topic subscription (you get three mails, but accepting once is enough).

## Step 3

__Clone infrastructure git__

Get the HTTPS Clone URL from the created AWS CodeCommit repository and clone the repository
```
git clone https://git-codecommit.eu-central-1.amazonaws.com/v1/repos/infra-deplyoment
```
Activate git flow (accept every default by pressing return
```
cd infra-deplyoment && git flow init
```

Copy content from `demo-templates` to the repository directory (adapt the pathes/folders to your demands)

```
cp -rp demo-templates/* ../infra-deplyoment/
cd ../ci-infra-test/` 
```

Push the code into the develop branch of the repository
```
git add --all && git commit -m "initial commit"
git push --set-upstream origin develop
```

# Step 4

## Test Pipelines 

In this step we test all three envirnments of the pipeline. Because we have an empty repository and create and fill all branches initially, it's deviating from the normal git flow usage.

### Develop / dev pipeline

After intial commit in the last step, the pipeline for the develop branch starts a run. After around 10 minutes this deployment should be successfully completed . You will find a mail for the stage "Deploy" in you inbox. In the Output of the stack `aws-demo-cf-cd-dev` you find a link to the S3 website deployed by this stage.

### Release / qa pipeline

To test the qa pipeline, we need to create a release and push it to the release branch. 

```
git flow release start demo_cf_v.1.00
git push --set-upstream origin release/demo_cf_v.1.00
```
After the push, the lambda function recognize the new branch and clone the Qa pipeline with the branch as source. Then the pipeline starts automaticlly and deploys the release.

###  Master/ prod pipeline

For the deplyoment to the prod environment, we can finish the release and merge it into the master branch (and also back to develop, if there are changes).

```
git flow release finish 'demo_cf_v.1.00'
git checkout master
git push
```

Or just push a change into the master branch.

### Feature and hotfixe

Feature and hotfixe aren't implemnetd yet. 

#### Feature

```
git flow feature start feature_xy
git add --all && git commit -m "feature XY added"

TODO: Check if necessary
git push --set-upstream origin feature

git flow feature finish feature_xy

```

#### Hotfix

```
git flow hotfix start demo_cf_v1.00_HF1
git flow hotfix finish demo_cf_v1.00_HF1
```

# Cleanup

> Be aware that all stacks are set to `DeletionPolicy: Delete`, your data is **lost** when you delete the stack.

To completly remove the deployment from your account, you need to clear first the content of the S3 buckets (or they can't be deleted together with the stack).
This includes
- the buckets created from the pipelines ($CloudFormationStackPrefix-{dev,qa,prod})
- the bucket created from the CodePipelineStack (BuildStatusBucket)
- the buckets created from the nested CodePipelineStack (PipelineArtifactsBucket)

Alternatively you can delete the buckets manually before or after you delete the main stack.

To speed this up, you could use the following code:

> **Warning** This code snippet deletes without a confirmation!
```
 aws s3 ls | grep $BUCKET_PREFIX* | awk '{ print $3 }' | while read line; do aws s3 rb s3://$line --force ; done;
```

If your CloudWatch Event rule has targets, the deletion could also fail. You need to delete these manually or delete the targets.

At last you can delete the S3 bucket you used initially for your deployment!

# Files

## config files

- .taskcat.yml

   *TODO: Add description here*

- template-configuration.json

   *TODO: Add description here*

## buildspec files

- buildspec

   Main build file, is used in the test stage for the taskcat tests and sync of the templates to the artifacts bucket.

- buildspec-cleanup

   Is used for the cleanup of the cloudformation templates in the artifacts bucket, also copies the `index.html` to the website bucket and inserts the stage in page.


# Bugs, open points and further development

Following enhancements are planed for the future:

- CodeCommit: Switch to a CloudWatch Events trigger instead of PollForSourceChanges 

- Lambda: Optimize code in the function (If..If..If construct, exception handling etc.)

- Add metrics and monitoring through CloudWatch

- Fix confused using of the wording "stage", which should be environment or similar

- Improve stack deletion and cleanup process, remove old build files etc.

- Send notifications with a nice template instead of JSON

- Implement git provider source dynamically instead of hard coded CodeCommit

- Build some kind of overview page for collected taskcat outputs

- Create multiple SNS topics or seperate somehow the notifications

- Create architecture overview images


## Bugs
Following bugs are known:

- In the deploy stage are the folders in the shared S3 bucket not dynamiclly created, which could lead to data integrity problems when multiple cloned pipelines and/or the master pipeline are running parallel (fix is in progress).
- It seems that the notifciations for failed build and succeeded "Deploy" stage aren't working

# Testing CF template

__Only on Linux!__

First configure aws on machine (if not already done)

`aws configure`

Use S3 Bucket from deploy and put it in `.taskcat.yml` or let taskcat create one (default) 

Run Tests
`taskcat test run -i cd-pipeline/.taskcat.yml`



# Sources


## CodePipeline / Lambda
https://aws.amazon.com/de/blogs/devops/adding-custom-logic-to-aws-codepipeline-with-aws-lambda-and-amazon-cloudwatch-events/


## Git (Flow)
https://danielkummer.github.io/git-flow-cheatsheet/index.html


https://aws.amazon.com/de/blogs/devops/implementing-gitflow-using-aws-codepipeline-aws-codecommit-aws-codebuild-and-aws-codedeploy/


## CloudWatch Events
https://docs.aws.amazon.com/codecommit/latest/userguide/monitoring-events.html