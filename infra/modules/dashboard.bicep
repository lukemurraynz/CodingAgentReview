param namePrefix string
param location string
param applicationInsightsId string
param serviceBusNamespaceId string
param serviceBusQueueName string
param cosmosAccountId string

var dashboardName = 'dash-${namePrefix}'
var dashboardTitle = 'Harness review operations'
var reviewRunFailureQuery = '''
union isfuzzy=true AppTraces
| where Message has 'annotation posting failed' or (Message startswith 'lens ' and Message has 'failed')
| summarize FailureCount = count() by bin(TimeGenerated, 5m)
| render timechart
'''
var timeRange = 'PT1H'

resource dashboard 'Microsoft.Portal/dashboards@2020-09-01-preview' = {
  name: dashboardName
  location: location
  tags: {
    'hidden-title': dashboardTitle
  }
  properties: {
    lenses: [
      {
        order: 0
        parts: [
          {
            position: {
              x: 0
              y: 0
              rowSpan: 4
              colSpan: 6
            }
            metadata: any({
              inputs: [
                {
                  name: 'Scope'
                  value: {
                    resourceIds: [
                      applicationInsightsId
                    ]
                  }
                }
                {
                  name: 'PartId'
                  value: guid(namePrefix, 'review-run-failures')
                }
                {
                  name: 'Version'
                  value: '2.0'
                }
                {
                  name: 'TimeRange'
                  value: timeRange
                }
                {
                  name: 'Query'
                  value: reviewRunFailureQuery
                }
                {
                  name: 'ControlType'
                  value: 'FrameControlChart'
                }
                {
                  name: 'SpecificChart'
                  value: 'Line'
                }
                {
                  name: 'IsQueryContainTimeRange'
                  value: false
                  isOptional: true
                }
              ]
              type: 'Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart'
              settings: {
                content: {
                  PartTitle: 'Failed review runs'
                  IsQueryContainTimeRange: false
                }
              }
              partHeader: {
                title: 'Failed review runs'
                subtitle: 'Application Insights traces'
              }
            })
          }
          {
            position: {
              x: 6
              y: 0
              rowSpan: 4
              colSpan: 6
            }
            metadata: any({
              inputs: [
                {
                  name: 'queryInputs'
                  value: {
                    timespan: {
                      duration: timeRange
                    }
                    id: serviceBusNamespaceId
                    chartType: 0
                    metrics: [
                      {
                        name: 'DeadletteredMessages'
                        resourceId: serviceBusNamespaceId
                        dimensions: [
                          {
                            name: 'EntityName'
                            operator: 'Include'
                            values: [
                              serviceBusQueueName
                            ]
                          }
                        ]
                      }
                    ]
                  }
                }
              ]
              type: 'Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart'
              partHeader: {
                title: 'Dead-letter messages'
                subtitle: 'Service Bus review queue'
              }
            })
          }
          {
            position: {
              x: 0
              y: 4
              rowSpan: 4
              colSpan: 6
            }
            metadata: any({
              inputs: [
                {
                  name: 'queryInputs'
                  value: {
                    timespan: {
                      duration: timeRange
                    }
                    id: cosmosAccountId
                    chartType: 0
                    metrics: [
                      {
                        name: 'TotalRequests'
                        resourceId: cosmosAccountId
                        dimensions: [
                          {
                            name: 'StatusCode'
                            operator: 'Include'
                            values: [
                              '429'
                            ]
                          }
                        ]
                      }
                    ]
                  }
                }
              ]
              type: 'Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart'
              partHeader: {
                title: 'Cosmos 429 responses'
                subtitle: 'Throttled request count'
              }
            })
          }
          {
            position: {
              x: 6
              y: 4
              rowSpan: 4
              colSpan: 6
            }
            metadata: any({
              inputs: [
                {
                  name: 'queryInputs'
                  value: {
                    timespan: {
                      duration: timeRange
                    }
                    id: serviceBusNamespaceId
                    chartType: 0
                    metrics: [
                      {
                        name: 'ActiveMessages'
                        resourceId: serviceBusNamespaceId
                        dimensions: [
                          {
                            name: 'EntityName'
                            operator: 'Include'
                            values: [
                              serviceBusQueueName
                            ]
                          }
                        ]
                      }
                    ]
                  }
                }
              ]
              type: 'Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart'
              partHeader: {
                title: 'Active queue backlog'
                subtitle: 'Service Bus review queue'
              }
            })
          }
        ]
      }
    ]
    metadata: {
      model: {
        timeRange: {
          type: 'MsPortalFx.Composition.Configuration.ValueTypes.TimeRange'
          value: {
            relative: {
              duration: 1
              timeUnit: 1
            }
          }
        }
      }
    }
  }
}

output dashboardId string = dashboard.id
