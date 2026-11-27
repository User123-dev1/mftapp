// AbelZeta app portal on Azure - deployed by .github/workflows/azure-deploy.yml into rg-abelzeta-portal.
//
//   vm-abz-apps (Ubuntu 24.04)  - the portal and apps in Docker (same as the AWS server), private IP 10.40.1.4
//   vm-abz-connector (Windows)  - Microsoft Entra private network connector (Application Proxy), 10.40.1.5
//   Key Vault                   - generated passwords and keys (read by vm-abz-apps with its managed identity)
//   Recovery Services vault     - daily backup of vm-abz-apps, 7 days kept
//
// Nothing accepts connections from the internet: users come in through Entra Application Proxy, whose
// connector makes only outbound connections. The public IPs exist only for outbound traffic (updates,
// image downloads) - the network security group denies all inbound from the internet, except an optional,
// temporary remote-desktop rule (rdpAllowedCidr) used once to install the connector.

@description('Azure region; defaults to the resource group\'s.')
param location string = resourceGroup().location

@description('Object ID of the GitHub deployment identity (given read/write on Key Vault secrets).')
param deployerObjectId string

@secure()
@description('Local administrator password of the Windows connector VM (generated once by the workflow).')
param windowsAdminPassword string

@description('SSH public key for the Linux VM. Its private key is not kept: the VM is managed with Run Command.')
param linuxSshPublicKey string

@description('Optional: one IP range (e.g. 203.0.113.25/32) allowed to use remote desktop on the connector VM. Empty = closed.')
param rdpAllowedCidr string = ''

param linuxVmSize string = 'Standard_B2s'
param windowsVmSize string = 'Standard_B2s'
param adminUsername string = 'abzadmin'
param tags object = { app: 'abelzeta-portal' }

var suffix = uniqueString(resourceGroup().id)
var kvName = 'kv-abz-${suffix}'
var appsIp = '10.40.1.4'
var connectorIp = '10.40.1.5'
var appsVmName = 'vm-abz-apps'
var connectorVmName = 'vm-abz-connector'
// v6 sizes (and some newer families) only attach disks over NVMe; older sizes use SCSI.
var linuxDiskController = contains(toLower(linuxVmSize), '_v6') ? 'NVMe' : 'SCSI'
var windowsDiskController = contains(toLower(windowsVmSize), '_v6') ? 'NVMe' : 'SCSI'
var rdpRule = empty(rdpAllowedCidr) ? [] : [
  {
    name: 'allow-rdp-temporary'
    properties: {
      priority: 200
      direction: 'Inbound'
      access: 'Allow'
      protocol: 'Tcp'
      sourceAddressPrefix: rdpAllowedCidr
      sourcePortRange: '*'
      destinationAddressPrefix: connectorIp
      destinationPortRange: '3389'
      description: 'Temporary: install the Entra connector, then remove (empty rdpAllowedCidr)'
    }
  }
]

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-abz'
  location: location
  tags: tags
  properties: {
    securityRules: concat(rdpRule, [
      {
        name: 'deny-internet-inbound'
        properties: {
          priority: 4000
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ])
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: 'vnet-abz'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.40.0.0/16'] }
    subnets: [
      {
        name: 'apps'
        properties: {
          addressPrefix: '10.40.1.0/24'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}

resource pipApps 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-abz-apps-outbound'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource pipConnector 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-abz-connector-outbound'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource nicApps 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: 'nic-abz-apps'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Static'
          privateIPAddress: appsIp
          publicIPAddress: { id: pipApps.id }
        }
      }
    ]
  }
}

resource nicConnector 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: 'nic-abz-connector'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Static'
          privateIPAddress: connectorIp
          publicIPAddress: { id: pipConnector.id }
        }
      }
    ]
  }
}

resource appsVm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: appsVmName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: linuxVmSize }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
    }
    storageProfile: {
      diskControllerType: linuxDiskController
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: {
        name: 'disk-abz-apps-os'
        createOption: 'FromImage'
        diskSizeGB: 64
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Detach'
      }
    }
    osProfile: {
      computerName: 'abz-apps'
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: { publicKeys: [{ path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: linuxSshPublicKey }] }
        patchSettings: { patchMode: 'ImageDefault' }
      }
    }
    networkProfile: { networkInterfaces: [{ id: nicApps.id }] }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}

resource connectorVm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: connectorVmName
  location: location
  tags: tags
  properties: {
    hardwareProfile: { vmSize: windowsVmSize }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
    }
    storageProfile: {
      diskControllerType: windowsDiskController
      imageReference: {
        publisher: 'MicrosoftWindowsServer'
        offer: 'WindowsServer'
        sku: '2022-datacenter-azure-edition-smalldisk'
        version: 'latest'
      }
      osDisk: {
        name: 'disk-abz-connector-os'
        createOption: 'FromImage'
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Delete'
      }
    }
    osProfile: {
      computerName: 'abz-connector'
      adminUsername: adminUsername
      adminPassword: windowsAdminPassword
      windowsConfiguration: { enableAutomaticUpdates: true, patchSettings: { patchMode: 'AutomaticByOS' } }
    }
    networkProfile: { networkInterfaces: [{ id: nicConnector.id }] }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: false
    enableSoftDelete: true
    softDeleteRetentionInDays: 30
    accessPolicies: [
      {
        tenantId: subscription().tenantId
        objectId: appsVm.identity.principalId
        permissions: { secrets: ['get', 'list', 'set'] }
      }
      {
        tenantId: subscription().tenantId
        objectId: deployerObjectId
        permissions: { secrets: ['get', 'list', 'set'] }
      }
    ]
  }
}

resource kvWinPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'connector-vm-admin-password'
  properties: { value: windowsAdminPassword, contentType: 'Local admin ${adminUsername} on ${connectorVmName}' }
}

resource kvSshKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'apps-vm-ssh-public-key'
  properties: { value: linuxSshPublicKey, contentType: 'public key (no private key is kept)' }
}

resource rsv 'Microsoft.RecoveryServices/vaults@2023-04-01' = {
  name: 'rsv-abz'
  location: location
  tags: tags
  sku: { name: 'RS0', tier: 'Standard' }
  properties: { publicNetworkAccess: 'Enabled' }
}

resource backupPolicy 'Microsoft.RecoveryServices/vaults/backupPolicies@2023-04-01' = {
  parent: rsv
  name: 'daily-7days'
  properties: {
    backupManagementType: 'AzureIaasVM'
    instantRpRetentionRangeInDays: 2
    timeZone: 'UTC'
    schedulePolicy: {
      schedulePolicyType: 'SimpleSchedulePolicy'
      scheduleRunFrequency: 'Daily'
      scheduleRunTimes: ['2026-01-01T05:00:00Z']
    }
    retentionPolicy: {
      retentionPolicyType: 'LongTermRetentionPolicy'
      dailySchedule: {
        retentionTimes: ['2026-01-01T05:00:00Z']
        retentionDuration: { count: 7, durationType: 'Days' }
      }
    }
  }
}

resource appsBackup 'Microsoft.RecoveryServices/vaults/backupFabrics/protectionContainers/protectedItems@2023-04-01' = {
  name: '${rsv.name}/Azure/iaasvmcontainer;iaasvmcontainerv2;${resourceGroup().name};${appsVmName}/vm;iaasvmcontainerv2;${resourceGroup().name};${appsVmName}'
  properties: {
    protectedItemType: 'Microsoft.Compute/virtualMachines'
    policyId: backupPolicy.id
    sourceResourceId: appsVm.id
  }
}

output keyVaultName string = kv.name
output appsVmName string = appsVm.name
output appsPrivateIp string = appsIp
output connectorVmName string = connectorVm.name
output connectorPublicIp string = pipConnector.properties.ipAddress
