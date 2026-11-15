@description('Name of the subscription system topic.')
param systemTopicName string

@description('Full resource ID of the monitored Azure subscription.')
param sourceSubscriptionId string

@description('Full resource ID of the published RbacAssignmentObserver function.')
param functionResourceId string

resource systemTopic 'Microsoft.EventGrid/systemTopics@2025-02-15' = {
  name: systemTopicName
  location: 'global'
  properties: {
    source: sourceSubscriptionId
    topicType: 'Microsoft.Resources.Subscriptions'
  }
}

resource eventSubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2025-02-15' = {
  parent: systemTopic
  name: 'rbac-role-assignment-writes'
  properties: {
    eventDeliverySchema: 'EventGridSchema'
    destination: {
      endpointType: 'AzureFunction'
      properties: {
        resourceId: functionResourceId
        maxEventsPerBatch: 1
      }
    }
    filter: {
      includedEventTypes: [
        'Microsoft.Resources.ResourceWriteSuccess'
      ]
      advancedFilters: [
        {
          operatorType: 'StringIn'
          key: 'data.operationName'
          values: [
            'Microsoft.Authorization/roleAssignments/write'
          ]
        }
      ]
    }
    retryPolicy: {
      maxDeliveryAttempts: 30
      eventTimeToLiveInMinutes: 1440
    }
  }
}
