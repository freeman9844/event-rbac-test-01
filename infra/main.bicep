targetScope = 'subscription'

@description('Name of a dedicated resource group for this proof of concept.')
@minLength(1)
param resourceGroupName string

@description('Azure region supporting Flex Consumption and Python 3.11.')
@minLength(1)
param location string

@description('A 3-16 character prefix using lowercase letters, digits and hyphens.')
@minLength(3)
@maxLength(16)
param namePrefix string

@description('Create event routing only after publishing the function. False does not delete existing routing.')
param enableEventSubscription bool = false

var suffix = uniqueString(subscription().id, resourceGroupName)
var topicName = 'evgt-${namePrefix}-${suffix}'
var readerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module workload 'workload.bicep' = {
  name: 'rbac-monitor-workload'
  scope: resourceGroup
  params: {
    location: location
    namePrefix: namePrefix
    suffix: suffix
  }
}

resource readerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, resourceGroupName, 'rbac-monitor-reader')
  properties: {
    roleDefinitionId: readerRoleId
    principalId: workload.outputs.managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

module eventing 'eventing.bicep' = if (enableEventSubscription) {
  name: 'rbac-monitor-eventing'
  scope: resourceGroup
  params: {
    systemTopicName: topicName
    sourceSubscriptionId: subscription().id
    functionResourceId: '${workload.outputs.functionAppResourceId}/functions/RbacAssignmentObserver'
  }
  dependsOn: [
    readerAssignment
  ]
}

output functionAppName string = workload.outputs.functionAppName
output functionAppResourceId string = workload.outputs.functionAppResourceId
output managedIdentityPrincipalId string = workload.outputs.managedIdentityPrincipalId
output applicationInsightsName string = workload.outputs.applicationInsightsName
output workspaceResourceId string = workload.outputs.workspaceResourceId
output systemTopicName string = topicName
