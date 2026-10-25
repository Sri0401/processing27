// Smart Campus OT data platform - Azure deployment (the AWS design lives in infra/).
//
//   Building gateway --HTTPS batch--> Container App: ingest-api --(shared handler.py)--> PostgreSQL B1ms
//                                            |                                            |
//                                            v                                            v
//                                     Blob Storage (raw)                     Container App: Grafana
//
// Built only from resource types allowed by the subscription's guardrail policies
// (free-tier SKUs, Australia East). See docs/adr/0008-azure-deployment.md.

targetScope = 'resourceGroup'

@description('Short environment name, used in resource names and tags.')
param envName string = 'dev'

param location string = resourceGroup().location

@description('Object id of the deploying user - granted Key Vault Secrets Officer so scripts can read secrets.')
param deployerObjectId string

@description('Public IP of the deploying machine, allowed through the PostgreSQL firewall for loading data and dbt.')
param deployerIp string

@description('Email for budget alerts.')
param alertEmail string

@description('Monthly budget for this resource group, in the billing currency. Everything here is meant to be free, so any spend at all should raise an alert.')
param monthlyBudget int = 1

@description('First day of the current month (budgets must start on the 1st).')
param budgetStartDate string = utcNow('yyyy-MM-01')

@description('Deploy the container apps. False on the first run, before images are published.')
param deployApps bool = false

@description('Ingest API image, e.g. ghcr.io/<owner>/smart-campus-platform:<sha>.')
param platformImage string = ''

@description('Grafana image with dashboards baked in, e.g. ghcr.io/<owner>/smart-campus-grafana:<sha>.')
param grafanaImage string = ''

@secure()
param pgAdminPassword string
@secure()
param ingestDbPassword string
@secure()
param transformDbPassword string
@secure()
param biDbPassword string
@secure()
param ingestApiKey string
@secure()
param grafanaAdminPassword string

var suffix = uniqueString(resourceGroup().id)
var prefix = 'smartcampus-${envName}'
var tags = {
  project: 'smart-campus'
  environment: envName
  owner: 'campus-operations-data'
  dataClassification: 'internal'
}
var dbName = 'smart_campus'

// Built-in role definition ids
var roleStorageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var roleKeyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var roleKeyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

// ------------------------------------------------------------------ identity
// One user-assigned identity for the apps: reads Key Vault secrets, writes the raw archive.
resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${prefix}'
  location: location
  tags: tags
}

// ------------------------------------------------------------------ observability
resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${prefix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    // Hard cap: 0.15 GB/day x 31 days = 4.65 GB < the 5 GB/month free allowance.
    workspaceCapping: { dailyQuotaGb: json('0.15') }
  }
}

// ------------------------------------------------------------------ raw landing zone
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stsc${envName}${take(suffix, 10)}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // identity-based access only - no account keys to leak
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

resource rawContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'raw'
  properties: { publicAccess: 'None' }
}

resource blobWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, appIdentity.id, roleStorageBlobDataContributor)
  scope: storage
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataContributor)
  }
}

// ------------------------------------------------------------------ secrets
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-sc-${envName}-${take(suffix, 8)}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    softDeleteRetentionInDays: 7
  }
}

var secretValues = {
  'pg-admin-password': pgAdminPassword
  'ingest-db-password': ingestDbPassword
  'transform-db-password': transformDbPassword
  'bi-db-password': biDbPassword
  'ingest-api-key': ingestApiKey
  'grafana-admin-password': grafanaAdminPassword
}

resource secrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for name in objectKeys(secretValues): {
  parent: vault
  name: name
  properties: { value: secretValues[name] }
}]

resource appReadsSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, appIdentity.id, roleKeyVaultSecretsUser)
  scope: vault
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsUser)
  }
}

resource deployerManagesSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, deployerObjectId, roleKeyVaultSecretsOfficer)
  scope: vault
  properties: {
    principalId: deployerObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsOfficer)
  }
}

// ------------------------------------------------------------------ warehouse
resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2022-12-01' = {
  name: 'psql-${prefix}-${take(suffix, 6)}'
  location: location
  tags: tags
  sku: { name: 'Standard_B1ms', tier: 'Burstable' } // free-tier eligible (750 h/month)
  properties: {
    version: '16'
    administratorLogin: 'platform_admin'
    administratorLoginPassword: pgAdminPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    authConfig: { activeDirectoryAuth: 'Disabled', passwordAuth: 'Enabled' }
  }
}

// Server parameters must be applied one at a time, hence the dependsOn chain.
resource pgExtensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2022-12-01' = {
  parent: postgres
  name: 'azure.extensions'
  properties: { value: 'TIMESCALEDB', source: 'user-override' }
}

resource pgPreload 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2022-12-01' = {
  parent: postgres
  name: 'shared_preload_libraries'
  properties: { value: 'pg_stat_statements,timescaledb', source: 'user-override' } // needs a restart
  dependsOn: [ pgExtensions ]
}

resource pgWorkMem 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2022-12-01' = {
  parent: postgres
  name: 'work_mem'
  properties: { value: '16384', source: 'user-override' } // 16 MB: window sorts in dbt stay in memory longer
  dependsOn: [ pgPreload ]
}

// Container Apps (consumption) has no fixed outbound IP, so Azure-internal traffic is allowed;
// TLS is enforced by the server and every role has its own strong password from Key Vault.
resource pgAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2022-12-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
  dependsOn: [ pgWorkMem ]
}

resource pgAllowDeployer 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2022-12-01' = {
  parent: postgres
  name: 'AllowDeployer'
  properties: { startIpAddress: deployerIp, endIpAddress: deployerIp }
  dependsOn: [ pgAllowAzure ]
}

// ------------------------------------------------------------------ compute
resource appsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${prefix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var kvSecret = {
  identity: appIdentity.id
}

resource ingestApp 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: 'ca-ingest-${envName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: appsEnv.id
    configuration: {
      ingress: { external: true, targetPort: 8080, transport: 'http', allowInsecure: false }
      secrets: [
        union(kvSecret, { name: 'ingest-db-password', keyVaultUrl: '${vault.properties.vaultUri}secrets/ingest-db-password' })
        union(kvSecret, { name: 'ingest-api-key', keyVaultUrl: '${vault.properties.vaultUri}secrets/ingest-api-key' })
      ]
    }
    template: {
      containers: [
        {
          name: 'ingest-api'
          image: platformImage
          command: [ 'python', '-m', 'smart_campus.ingest_api' ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'PORT', value: '8080' }
            { name: 'DB_HOST', value: postgres.properties.fullyQualifiedDomainName }
            { name: 'DB_NAME', value: dbName }
            { name: 'DB_USER', value: 'ingest_writer' }
            { name: 'DB_PASSWORD', secretRef: 'ingest-db-password' }
            { name: 'DB_SSLMODE', value: 'require' }
            { name: 'INGEST_API_KEY', secretRef: 'ingest-api-key' }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
            { name: 'RAW_CONTAINER', value: rawContainer.name }
            { name: 'AZURE_CLIENT_ID', value: appIdentity.properties.clientId }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8080 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/readyz', port: 8080 }, periodSeconds: 15, failureThreshold: 4 }
          ]
        }
      ]
      scale: {
        minReplicas: 0 // scale to zero between gateway posts - stays inside the free grant
        maxReplicas: 2
        rules: [ { name: 'http', http: { metadata: { concurrentRequests: '20' } } } ]
      }
    }
  }
  dependsOn: [ appReadsSecrets, blobWriter, secrets ]
}

resource grafanaApp 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: 'ca-grafana-${envName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: appsEnv.id
    configuration: {
      ingress: { external: true, targetPort: 3000, transport: 'http', allowInsecure: false }
      secrets: [
        union(kvSecret, { name: 'bi-db-password', keyVaultUrl: '${vault.properties.vaultUri}secrets/bi-db-password' })
        union(kvSecret, { name: 'grafana-admin-password', keyVaultUrl: '${vault.properties.vaultUri}secrets/grafana-admin-password' })
      ]
    }
    template: {
      containers: [
        {
          name: 'grafana'
          image: grafanaImage
          // Smallest size: a public URL may be woken by bots, so stretch the free grant (~200 active h/month).
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'CAMPUS_DB_HOST', value: postgres.properties.fullyQualifiedDomainName }
            { name: 'CAMPUS_DB_SSLMODE', value: 'require' }
            { name: 'POSTGRES_DB', value: dbName }
            { name: 'BI_DB_PASSWORD', secretRef: 'bi-db-password' }
            { name: 'GF_SECURITY_ADMIN_PASSWORD', secretRef: 'grafana-admin-password' }
            { name: 'GF_AUTH_ANONYMOUS_ENABLED', value: 'true' } // read-only public demo
            { name: 'GF_AUTH_ANONYMOUS_ORG_ROLE', value: 'Viewer' }
            { name: 'GF_USERS_DEFAULT_THEME', value: 'light' }
            { name: 'GF_ANALYTICS_REPORTING_ENABLED', value: 'false' }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
  dependsOn: [ appReadsSecrets, secrets ]
}

// ------------------------------------------------------------------ cost guardrail
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-${prefix}'
  properties: {
    category: 'Cost'
    amount: monthlyBudget
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual50: { enabled: true, operator: 'GreaterThan', threshold: 50, contactEmails: [ alertEmail ], thresholdType: 'Actual' }
      actual90: { enabled: true, operator: 'GreaterThan', threshold: 90, contactEmails: [ alertEmail ], thresholdType: 'Actual' }
      forecast100: { enabled: true, operator: 'GreaterThan', threshold: 100, contactEmails: [ alertEmail ], thresholdType: 'Forecasted' }
    }
  }
}

// ------------------------------------------------------------------ outputs
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output postgresServerName string = postgres.name
output keyVaultName string = vault.name
output storageBlobEndpoint string = storage.properties.primaryEndpoints.blob
output ingestUrl string = deployApps ? 'https://${ingestApp!.properties.configuration.ingress.fqdn}' : ''
output grafanaUrl string = deployApps ? 'https://${grafanaApp!.properties.configuration.ingress.fqdn}' : ''
