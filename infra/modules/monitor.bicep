param namePrefix string
param location string
param applicationInsightsId string
param serviceBusNamespaceId string
param serviceBusQueueName string
param cosmosAccountId string
param backlogThreshold int = 25
param cosmos429Threshold int = 100

// Cold workspaces lack the AppTraces table; flip on after first worker telemetry.
param enableReviewRunFailureAlert bool = false

var reviewRunFailureQuery = '''
union isfuzzy=true AppTraces
| where Message has 'annotation posting failed' or (Message startswith 'lens ' and Message has 'failed')
| summarize FailureCount = count() by bin(TimeGenerated, 5m)
'''

resource queueDeadLetterAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-${namePrefix}-sb-deadletter'
  location: 'global'
  properties: {
    description: 'Dead-lettered review requests detected on the review queue.'
    severity: 2
    enabled: true
    scopes: [
      serviceBusNamespaceId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'reviewQueueDeadletters'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'DeadletteredMessages'
          dimensions: [
            {
              name: 'EntityName'
              operator: 'Include'
              values: [
                serviceBusQueueName
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Maximum'
        }
      ]
    }
  }
}

resource queueBacklogAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-${namePrefix}-sb-backlog'
  location: 'global'
  properties: {
    description: 'Active review queue backlog is above the configured threshold.'
    severity: 3
    enabled: true
    scopes: [
      serviceBusNamespaceId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'reviewQueueBacklog'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'ActiveMessages'
          dimensions: [
            {
              name: 'EntityName'
              operator: 'Include'
              values: [
                serviceBusQueueName
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: backlogThreshold
          timeAggregation: 'Maximum'
        }
      ]
    }
  }
}

resource cosmosThrottleAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-${namePrefix}-cosmos-429'
  location: 'global'
  properties: {
    description: 'Cosmos DB requests are being throttled with HTTP 429 responses.'
    severity: 2
    enabled: true
    scopes: [
      cosmosAccountId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'cosmos429Requests'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'TotalRequests'
          dimensions: [
            {
              name: 'StatusCode'
              operator: 'Include'
              values: [
                '429'
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: cosmos429Threshold
          timeAggregation: 'Count'
        }
      ]
    }
  }
}

resource reviewRunFailureAlert 'Microsoft.Insights/scheduledQueryRules@2021-08-01' = if (enableReviewRunFailureAlert) {
  name: 'alert-${namePrefix}-review-run-failures'
  location: location
  properties: {
    description: 'Worker review-run failure traces were emitted to Application Insights.'
    enabled: true
    severity: 2
    scopes: [
      applicationInsightsId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      allOf: [
        {
          query: reviewRunFailureQuery
          metricMeasureColumn: 'FailureCount'
          dimensions: []
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
  }
}
