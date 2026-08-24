"""Azure Resource Graph Service using Azure SDK directly."""

import asyncio
import threading
import time
from typing import Any, Optional

from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest
from structlog import get_logger

from src.config import get_settings

logger = get_logger()

# Module-level cache for resource types summary (shared across all service instances)
_resource_types_cache: Optional[dict[str, Any]] = None
_resource_types_cache_time: float = 0
_resource_types_cache_lock = threading.Lock()
_RESOURCE_TYPES_CACHE_TTL = 300  # 5 minutes TTL


class ResourceGraphService:
    """Service for Azure Resource Graph queries using Azure SDK directly."""

    def __init__(self, subscription_id: Optional[str] = None):
        """Initialize Resource Graph service.

        Args:
            subscription_id: Azure subscription ID (uses config if not provided)
        """
        settings = get_settings()
        self.subscription_id = subscription_id or settings.azure_subscription_id
        self._client: Optional[ResourceGraphClient] = None
        self._credential = None
        self._discovered_subscriptions: Optional[list[str]] = None
        self._subscription_name_map: dict[str, str] = {}

    def _get_credential(self):
        """Get or create Azure credential."""
        if self._credential is None:
            from src.config import get_azure_credential

            self._credential = get_azure_credential()
        return self._credential

    def _get_client(self) -> ResourceGraphClient:
        """Get or create Resource Graph client.

        Uses DefaultAzureCredential which tries multiple auth methods:
        - EnvironmentCredential (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID)
        - AzureCliCredential (az login)
        - ManagedIdentityCredential (when running in Azure)
        """
        if self._client is None:
            self._client = ResourceGraphClient(self._get_credential())
        return self._client

    async def _discover_accessible_subscriptions_async(self) -> list[str]:
        """Discover all enabled subscriptions accessible by current credential (async)."""
        if self._discovered_subscriptions is not None:
            return self._discovered_subscriptions

        from src.services import discover_subscriptions_async

        _t0 = time.time()
        credential = self._get_credential()
        subs = await discover_subscriptions_async(credential)

        self._discovered_subscriptions = [s["subscriptionId"] for s in subs]
        for s in subs:
            if s["displayName"]:
                self._subscription_name_map[s["subscriptionId"]] = s["displayName"]

        _elapsed = time.time() - _t0
        logger.info(
            "subscriptions_discovered",
            count=len(self._discovered_subscriptions),
            elapsed_s=round(_elapsed, 2),
        )
        return self._discovered_subscriptions

    def _discover_accessible_subscriptions(self) -> list[str]:
        """Discover all enabled subscriptions (sync fallback for non-async callers)."""
        if self._discovered_subscriptions is not None:
            return self._discovered_subscriptions

        from src.services import discover_subscriptions_sync

        _t0 = time.time()
        credential = self._get_credential()
        subs = discover_subscriptions_sync(credential)

        self._discovered_subscriptions = [s["subscriptionId"] for s in subs]
        for s in subs:
            if s["displayName"]:
                self._subscription_name_map[s["subscriptionId"]] = s["displayName"]

        _elapsed = time.time() - _t0
        logger.info(
            "subscriptions_discovered",
            count=len(self._discovered_subscriptions),
            elapsed_s=round(_elapsed, 2),
        )
        return self._discovered_subscriptions

    def get_subscription_name(self, subscription_id: str) -> str:
        """Get subscription display name for a subscription ID.

        Args:
            subscription_id: Azure subscription ID (GUID)

        Returns:
            Subscription display name, or the ID itself if name is unknown
        """
        if not self._subscription_name_map:
            # Trigger discovery to populate the map
            self._discover_accessible_subscriptions()
        return self._subscription_name_map.get(subscription_id, subscription_id)

    def get_subscription_name_map(self) -> dict[str, str]:
        """Get the full subscription ID to display name mapping.

        Returns:
            Dictionary mapping subscription IDs to display names
        """
        if not self._subscription_name_map:
            self._discover_accessible_subscriptions()
        return dict(self._subscription_name_map)

    def enrich_subscription_names(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Enrich resource data by resolving subscriptionId to display name.

        Adds a ``subscriptionName`` field next to ``subscriptionId`` for every
        record that contains a ``subscriptionId`` value.

        Args:
            data: List of resource dicts from a Resource Graph query result.

        Returns:
            The same list with ``subscriptionName`` fields added in-place.
        """
        if not data:
            return data

        name_map = self.get_subscription_name_map()
        for record in data:
            sub_id = record.get("subscriptionId")
            if sub_id and isinstance(sub_id, str):
                record["subscriptionName"] = name_map.get(sub_id, sub_id)
        return data

    async def query_resources(
        self, query: str, subscriptions: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """Execute a Resource Graph query.

        Args:
            query: KQL query string
            subscriptions: List of subscription IDs (optional, uses default if not provided)

        Returns:
            Query results with data, count, and total_records
        """
        if subscriptions is None:
            if self.subscription_id:
                subscriptions = [self.subscription_id]
            else:
                subscriptions = await self._discover_accessible_subscriptions_async()
                if not subscriptions:
                    raise ValueError(
                        "No accessible Azure subscriptions found for tenant-wide query. "
                        "Set AZURE_SUBSCRIPTION_ID or ensure your identity has access to at least one enabled subscription."
                    )

        # Sanitize common KQL issues before executing
        query = self._sanitize_query(query)

        logger.info(
            "resource_graph_query",
            query=query[:500],
            subscription_count=len(subscriptions),
        )

        try:
            client = self._get_client()
            request = QueryRequest(
                subscriptions=subscriptions,
                query=query,
            )

            _t0 = time.time()
            # Run sync SDK call in a thread to avoid blocking the event loop
            response = await asyncio.to_thread(client.resources, request)
            _elapsed = time.time() - _t0

            result = {
                "data": response.data,
                "count": response.count,
                "total_records": response.total_records,
            }

            logger.info(
                "resource_graph_query_ok",
                count=response.count,
                total_records=response.total_records,
                elapsed_s=round(_elapsed, 2),
            )

            return result

        except Exception as e:
            logger.error("resource_graph_query_error", error=str(e), query=query[:300])
            raise

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """Sanitize KQL query to fix common issues before execution.

        Delegates to the comprehensive sanitize_kql() in tools module for
        queries run through execute_kql_with_retry. For direct query_resources
        calls, applies the critical '| top N' fix only (lightweight).
        """
        from src.agent.tools import _RE_STRAY_TOP, _RE_TOP_WITHOUT_BY

        query = _RE_TOP_WITHOUT_BY.sub(r"| take \1", query)
        query = _RE_STRAY_TOP.sub(r" | take \1", query)
        return query

    async def get_resource_types_summary(self) -> dict[str, Any]:
        """Get summary of resource types in the subscription.

        Returns cached result if available within TTL to avoid duplicate queries.
        Thread-safe cache access for concurrent requests.

        Returns:
            Summary with resource types and counts
        """
        global _resource_types_cache, _resource_types_cache_time

        # Thread-safe read
        with _resource_types_cache_lock:
            if (
                _resource_types_cache
                and (time.time() - _resource_types_cache_time) < _RESOURCE_TYPES_CACHE_TTL
            ):
                cache_age = round(time.time() - _resource_types_cache_time, 1)
                logger.info("resource_types_cache_hit", cache_age_s=cache_age)
                return _resource_types_cache

        logger.debug("resource_types_cache_miss")
        query = ResourceGraphQueryBuilder.get_resource_types_summary()
        result = await self.query_resources(query)

        # Thread-safe write
        with _resource_types_cache_lock:
            _resource_types_cache = result
            _resource_types_cache_time = time.time()
        return result

    async def find_related_resources(self, service_keywords: list[str]) -> dict[str, Any]:
        """Find resources related to given service keywords.

        Args:
            service_keywords: List of keywords to search for

        Returns:
            Query results
        """
        query = ResourceGraphQueryBuilder.find_related_resources(service_keywords)
        return await self.query_resources(query)


class ResourceGraphQueryBuilder:
    """Helper class to build common Resource Graph queries."""

    @staticmethod
    def _sanitize_kql_value(value: str) -> str:
        """Sanitize a string value for safe embedding in KQL queries.

        Prevents KQL injection by removing characters that could break
        out of a quoted string literal or inject operators.

        Args:
            value: Raw string value (e.g., from LLM output)

        Returns:
            Sanitized string safe for KQL single-quoted literals
        """
        # Remove single quotes (break out of string literal)
        value = value.replace("'", "")
        # Remove backslashes (escape sequences)
        value = value.replace("\\", "")
        # Remove semicolons (statement separator)
        value = value.replace(";", "")
        # Remove pipe characters (KQL operator chaining)
        value = value.replace("|", "")
        # Collapse whitespace to single spaces
        value = " ".join(value.split())
        # Limit length to prevent abuse
        return value[:200]

    # =========================================================================
    # Basic Queries
    # =========================================================================

    @staticmethod
    def list_resources_by_service(service_name: str) -> str:
        """Query to list resources related to a service.

        Args:
            service_name: Service name to search for
        """
        safe = ResourceGraphQueryBuilder._sanitize_kql_value(service_name)
        return f"""
        Resources
        | where type contains '{safe}' or name contains '{safe}'
        | project id, name, type, location, resourceGroup, subscriptionId, tags, sku, properties
        | order by type asc
        """

    @staticmethod
    def get_resource_types_summary() -> str:
        """Query to get summary of resource types."""
        return """
        Resources
        | summarize count() by type
        | order by count_ desc
        """

    @staticmethod
    def get_resource_regions_summary() -> str:
        """Query to get resource count by region (location)."""
        return """
        Resources
        | summarize count() by location
        | order by count_ desc
        """

    @staticmethod
    def get_service_region_matrix() -> str:
        """Query to get which services exist in which regions."""
        return """
        Resources
        | summarize count() by type, location
        | order by type asc, count_ desc
        """

    @staticmethod
    def find_related_resources(service_keywords: list[str]) -> str:
        """Query to find resources related to given service keywords.

        Projects identity fields only. ``tags``/``sku``/``properties`` are raw
        JSON blobs that made every result overflow the prompt budget, and
        callers that need them have per-service detail queries.

        Args:
            service_keywords: List of keywords to search for
        """
        sanitize = ResourceGraphQueryBuilder._sanitize_kql_value
        safe_keywords = [sanitize(kw) for kw in service_keywords if kw.strip()]
        if not safe_keywords:
            safe_keywords = [""]
        conditions = " or ".join([f"type contains '{kw}'" for kw in safe_keywords])
        return f"""
        Resources
        | where {conditions}
        | project name, type, location, resourceGroup, subscriptionId
        | order by type asc, name asc
        """

    # =========================================================================
    # Storage Account Queries
    # =========================================================================

    @staticmethod
    def get_storage_accounts_detail() -> str:
        """Get detailed storage account information including security, networking, and features."""
        return """
        Resources
        | where type =~ 'Microsoft.Storage/storageAccounts'
        | extend accessTier = properties.accessTier
        | extend enableHttpsTrafficOnly = properties.supportsHttpsTrafficOnly
        | extend isHnsEnabled = properties.isHnsEnabled
        | extend isSftpEnabled = properties.isSftpEnabled
        | extend minimumTlsVersion = properties.minimumTlsVersion
        | extend allowBlobPublicAccess = properties.allowBlobPublicAccess
        | extend allowSharedKeyAccess = properties.allowSharedKeyAccess
        | extend networkAcls = properties.networkAcls.defaultAction
        | extend publicNetworkAccess = properties.publicNetworkAccess
        | extend privateEndpoints = array_length(properties.privateEndpointConnections)
        | extend primaryLocation = properties.primaryLocation
        | extend secondaryLocation = properties.secondaryLocation
        | extend largeFileSharesState = properties.largeFileSharesState
        | extend infrastructureEncryption = properties.encryption.requireInfrastructureEncryption
        | extend keySource = properties.encryption.keySource
        | extend skuName = sku.name
        | extend skuTier = sku.tier
        | project name, resourceGroup, subscriptionId, location, skuName, skuTier, accessTier, 
                  isHnsEnabled, isSftpEnabled, enableHttpsTrafficOnly, 
                  minimumTlsVersion, allowBlobPublicAccess, allowSharedKeyAccess,
                  networkAcls, publicNetworkAccess, privateEndpoints, secondaryLocation,
                  largeFileSharesState, infrastructureEncryption, keySource
        | order by name asc
        """

    # =========================================================================
    # Virtual Machine Queries
    # =========================================================================

    @staticmethod
    def get_virtual_machines_detail() -> str:
        """Get detailed VM information including size, OS, security, and diagnostics."""
        return """
        Resources
        | where type =~ 'Microsoft.Compute/virtualMachines'
        | extend vmSize = properties.hardwareProfile.vmSize
        | extend osType = properties.storageProfile.osDisk.osType
        | extend osPublisher = properties.storageProfile.imageReference.publisher
        | extend osOffer = properties.storageProfile.imageReference.offer
        | extend osSku = properties.storageProfile.imageReference.sku
        | extend osVersion = properties.storageProfile.imageReference.version
        | extend provisioningState = properties.provisioningState
        | extend licenseType = properties.licenseType
        | extend securityType = properties.securityProfile.securityType
        | extend secureBootEnabled = properties.securityProfile.uefiSettings.secureBootEnabled
        | extend vTpmEnabled = properties.securityProfile.uefiSettings.vTpmEnabled
        | extend encryptionAtHost = properties.securityProfile.encryptionAtHost
        | extend osDiskType = properties.storageProfile.osDisk.managedDisk.storageAccountType
        | extend osDiskSizeGB = properties.storageProfile.osDisk.diskSizeGB
        | extend dataDisksCount = array_length(properties.storageProfile.dataDisks)
        | extend nicCount = array_length(properties.networkProfile.networkInterfaces)
        | extend availabilityZone = zones[0]
        | project name, resourceGroup, subscriptionId, location, vmSize, osType, 
                  osPublisher, osOffer, osSku, osVersion, provisioningState, licenseType,
                  securityType, secureBootEnabled, vTpmEnabled, encryptionAtHost,
                  osDiskType, osDiskSizeGB, dataDisksCount, nicCount, availabilityZone, tags
        | order by name asc
        """

    # =========================================================================
    # Network Queries
    # =========================================================================

    @staticmethod
    def get_network_security_groups_detail() -> str:
        """Get NSG details including rules count."""
        return """
        Resources
        | where type =~ 'Microsoft.Network/networkSecurityGroups'
        | extend securityRulesCount = array_length(properties.securityRules)
        | extend defaultSecurityRulesCount = array_length(properties.defaultSecurityRules)
        | project name, resourceGroup, subscriptionId, location, securityRulesCount, 
                  defaultSecurityRulesCount, provisioningState = properties.provisioningState
        """

    @staticmethod
    def get_virtual_networks_detail() -> str:
        """Get VNet details including address space and subnets."""
        return """
        Resources
        | where type =~ 'Microsoft.Network/virtualNetworks'
        | extend addressPrefixes = properties.addressSpace.addressPrefixes
        | extend subnetCount = array_length(properties.subnets)
        | extend enableDdosProtection = properties.enableDdosProtection
        | project name, resourceGroup, subscriptionId, location, addressPrefixes, subnetCount, 
                  enableDdosProtection, provisioningState = properties.provisioningState
        """

    @staticmethod
    def get_public_ip_addresses() -> str:
        """Get public IP addresses with allocation method."""
        return """
        Resources
        | where type =~ 'Microsoft.Network/publicIPAddresses'
        | extend ipAddress = properties.ipAddress
        | extend allocationMethod = properties.publicIPAllocationMethod
        | extend ipVersion = properties.publicIPAddressVersion
        | extend skuName = sku.name
        | project name, resourceGroup, subscriptionId, location, ipAddress, allocationMethod, 
                  ipVersion, skuName
        """

    # =========================================================================
    # Container & Kubernetes Queries
    # =========================================================================

    @staticmethod
    def get_aks_clusters_detail() -> str:
        """Get AKS cluster details including addons, RBAC, auto-upgrade, and node pools."""
        return """
        Resources
        | where type =~ 'Microsoft.ContainerService/managedClusters'
        | extend kubernetesVersion = properties.kubernetesVersion
        | extend currentKubernetesVersion = properties.currentKubernetesVersion
        | extend powerState = properties.powerState.code
        | extend provisioningState = properties.provisioningState
        | extend nodeResourceGroup = properties.nodeResourceGroup
        | extend networkPlugin = properties.networkProfile.networkPlugin
        | extend networkPolicy = properties.networkProfile.networkPolicy
        | extend networkDataplane = properties.networkProfile.networkDataplane
        | extend advancedNetworkingObservability = properties.networkProfile.advancedNetworking.observability.enabled
        | extend advancedNetworkingSecurity = properties.networkProfile.advancedNetworking.security.enabled
        | extend loadBalancerSku = properties.networkProfile.loadBalancerSku
        | extend serviceCidr = properties.networkProfile.serviceCidr
        | extend dnsServiceIP = properties.networkProfile.dnsServiceIP
        | extend outboundType = properties.networkProfile.outboundType
        | extend agentPoolCount = array_length(properties.agentPoolProfiles)
        | extend enableRBAC = properties.enableRBAC
        | extend aadEnabled = isnotnull(properties.aadProfile)
        | extend localAccountDisabled = properties.disableLocalAccounts
        | extend autoUpgradeChannel = properties.autoUpgradeProfile.upgradeChannel
        | extend nodeOsUpgradeChannel = properties.autoUpgradeProfile.nodeOSUpgradeChannel
        | extend addonHttpRouting = properties.addonProfiles.httpApplicationRouting.enabled
        | extend addonMonitoring = properties.addonProfiles.omsagent.enabled
        | extend addonPolicy = properties.addonProfiles.azurepolicy.enabled
        | extend addonKeyVaultCSI = properties.addonProfiles.azureKeyvaultSecretsProvider.enabled
        | extend addonAppRouting = properties.addonProfiles.ingressApplicationGateway.enabled
        | extend addonDefender = properties.securityProfile.defender.securityMonitoring.enabled
        | extend addonWorkloadIdentity = properties.securityProfile.workloadIdentity.enabled
        | extend addonImageCleaner = properties.securityProfile.imageCleaner.enabled
        | extend privateFqdn = properties.privateFQDN
        | extend apiServerAccessProfile = properties.apiServerAccessProfile.authorizedIPRanges
        | extend skuTier = sku.tier
        | project name, resourceGroup, subscriptionId, location, kubernetesVersion,
                  currentKubernetesVersion, powerState, provisioningState,
                  networkPlugin, networkPolicy, networkDataplane, loadBalancerSku,
                  advancedNetworkingObservability, advancedNetworkingSecurity,
                  outboundType, agentPoolCount,
                  enableRBAC, aadEnabled, localAccountDisabled,
                  autoUpgradeChannel, nodeOsUpgradeChannel,
                  addonHttpRouting, addonMonitoring, addonPolicy,
                  addonKeyVaultCSI, addonAppRouting, addonDefender,
                  addonWorkloadIdentity, addonImageCleaner,
                  privateFqdn, skuTier
        """

    @staticmethod
    def get_container_registries() -> str:
        """Get Container Registry details."""
        return """
        Resources
        | where type =~ 'Microsoft.ContainerRegistry/registries'
        | extend skuName = sku.name
        | extend adminEnabled = properties.adminUserEnabled
        | extend publicNetworkAccess = properties.publicNetworkAccess
        | extend zoneRedundancy = properties.zoneRedundancy
        | project name, resourceGroup, subscriptionId, location, skuName, adminEnabled, 
                  publicNetworkAccess, zoneRedundancy
        """

    # =========================================================================
    # Database Queries
    # =========================================================================

    @staticmethod
    def get_sql_databases() -> str:
        """Get SQL Database details including SKU, security, and high availability."""
        return """
        Resources
        | where type =~ 'Microsoft.Sql/servers/databases'
        | extend serverName = tostring(split(id, '/')[8])
        | extend skuName = sku.name
        | extend skuTier = sku.tier
        | extend skuCapacity = sku.capacity
        | extend maxSizeBytes = properties.maxSizeBytes
        | extend status = properties.status
        | extend zoneRedundant = properties.zoneRedundant
        | extend readScale = properties.readScale
        | extend currentBackupStorageRedundancy = properties.currentBackupStorageRedundancy
        | extend requestedBackupStorageRedundancy = properties.requestedBackupStorageRedundancy
        | extend maintenanceConfigurationId = tostring(split(properties.maintenanceConfigurationId, '/')[-1])
        | extend isLedgerOn = properties.isLedgerOn
        | extend licenseType = properties.licenseType
        | project name, serverName, resourceGroup, subscriptionId, location, skuName, skuTier, 
                  skuCapacity, maxSizeBytes, status, zoneRedundant, readScale,
                  currentBackupStorageRedundancy, maintenanceConfigurationId,
                  isLedgerOn, licenseType
        """

    @staticmethod
    def get_cosmos_accounts() -> str:
        """Get Cosmos DB account details."""
        return """
        Resources
        | where type =~ 'Microsoft.DocumentDB/databaseAccounts'
        | extend apiKind = kind
        | extend consistencyLevel = properties.consistencyPolicy.defaultConsistencyLevel
        | extend enableMultipleWriteLocations = properties.enableMultipleWriteLocations
        | extend enableAutomaticFailover = properties.enableAutomaticFailover
        | extend publicNetworkAccess = properties.publicNetworkAccess
        | extend backupPolicyType = properties.backupPolicy.type
        | extend enableAnalyticalStorage = properties.enableAnalyticalStorage
        | extend disableLocalAuth = properties.disableLocalAuth
        | project name, resourceGroup, subscriptionId, location, apiKind, consistencyLevel, 
                  enableMultipleWriteLocations, enableAutomaticFailover, publicNetworkAccess,
                  backupPolicyType, enableAnalyticalStorage, disableLocalAuth
        """

    # =========================================================================
    # Serverless & App Queries
    # =========================================================================

    @staticmethod
    def get_function_apps_detail() -> str:
        """Get Function App details including runtime, hosting plan, and security settings."""
        return """
        Resources
        | where type =~ 'Microsoft.Web/sites' and kind contains 'functionapp'
        | extend runtimeVersion = properties.siteConfig.netFrameworkVersion
        | extend linuxFxVersion = properties.siteConfig.linuxFxVersion
        | extend pythonVersion = properties.siteConfig.pythonVersion
        | extend nodeVersion = properties.siteConfig.nodeVersion
        | extend javaVersion = properties.siteConfig.javaVersion
        | extend powerShellVersion = properties.siteConfig.powerShellVersion
        | extend state = properties.state
        | extend httpsOnly = properties.httpsOnly
        | extend ftpsState = properties.siteConfig.ftpsState
        | extend minTlsVersion = properties.siteConfig.minTlsVersion
        | extend http20Enabled = properties.siteConfig.http20Enabled
        | extend managedPipelineMode = properties.siteConfig.managedPipelineMode
        | extend vnetRouteAllEnabled = properties.vnetRouteAllEnabled
        | extend publicNetworkAccess = properties.publicNetworkAccess
        | extend hostingPlan = tostring(split(properties.serverFarmId, '/')[-1])
        | project name, resourceGroup, subscriptionId, location, kind, state,
                  runtimeVersion, linuxFxVersion, pythonVersion, nodeVersion, javaVersion,
                  httpsOnly, ftpsState, minTlsVersion, http20Enabled,
                  vnetRouteAllEnabled, publicNetworkAccess, hostingPlan
        """

    @staticmethod
    def get_app_services() -> str:
        """Get App Service / Web App details including runtime and networking."""
        return """
        Resources
        | where type =~ 'Microsoft.Web/sites' and kind !contains 'functionapp'
        | extend state = properties.state
        | extend httpsOnly = properties.httpsOnly
        | extend ftpsState = properties.siteConfig.ftpsState
        | extend minTlsVersion = properties.siteConfig.minTlsVersion
        | extend http20Enabled = properties.siteConfig.http20Enabled
        | extend linuxFxVersion = properties.siteConfig.linuxFxVersion
        | extend netFrameworkVersion = properties.siteConfig.netFrameworkVersion
        | extend vnetRouteAllEnabled = properties.vnetRouteAllEnabled
        | extend publicNetworkAccess = properties.publicNetworkAccess
        | extend alwaysOn = properties.siteConfig.alwaysOn
        | extend hostingPlan = tostring(split(properties.serverFarmId, '/')[-1])
        | project name, resourceGroup, subscriptionId, location, kind, state, 
                  httpsOnly, ftpsState, minTlsVersion, http20Enabled,
                  linuxFxVersion, netFrameworkVersion,
                  vnetRouteAllEnabled, publicNetworkAccess, alwaysOn, hostingPlan
        """

    @staticmethod
    def get_container_apps() -> str:
        """Get Container Apps details including ingress, scaling, and Dapr configuration."""
        return """
        Resources
        | where type =~ 'Microsoft.App/containerApps'
        | extend managedEnv = tostring(split(properties.managedEnvironmentId, '/')[-1])
        | extend runningStatus = properties.runningStatus
        | extend provisioningState = properties.provisioningState
        | extend ingressEnabled = properties.configuration.ingress.external
        | extend ingressTargetPort = properties.configuration.ingress.targetPort
        | extend ingressTransport = properties.configuration.ingress.transport
        | extend minReplicas = properties.template.scale.minReplicas
        | extend maxReplicas = properties.template.scale.maxReplicas
        | extend scaleRuleCount = array_length(properties.template.scale.rules)
        | extend daprEnabled = properties.configuration.dapr.enabled
        | extend containerCount = array_length(properties.template.containers)
        | extend revisionMode = properties.configuration.activeRevisionsMode
        | project name, resourceGroup, subscriptionId, location, managedEnv, runningStatus,
                  provisioningState, ingressEnabled, ingressTargetPort, ingressTransport,
                  minReplicas, maxReplicas, scaleRuleCount, daprEnabled,
                  containerCount, revisionMode
        """

    # =========================================================================
    # Security & Monitoring Queries
    # =========================================================================

    @staticmethod
    def get_key_vaults() -> str:
        """Get Key Vault details including security and access configuration."""
        return """
        Resources
        | where type =~ 'Microsoft.KeyVault/vaults'
        | extend skuName = properties.sku.name
        | extend enableSoftDelete = properties.enableSoftDelete
        | extend softDeleteRetentionInDays = properties.softDeleteRetentionInDays
        | extend enablePurgeProtection = properties.enablePurgeProtection
        | extend enableRbacAuthorization = properties.enableRbacAuthorization
        | extend publicNetworkAccess = properties.publicNetworkAccess
        | extend privateEndpoints = array_length(properties.privateEndpointConnections)
        | extend networkDefaultAction = properties.networkAcls.defaultAction
        | project name, resourceGroup, subscriptionId, location, skuName, enableSoftDelete,
                  softDeleteRetentionInDays, enablePurgeProtection, enableRbacAuthorization,
                  publicNetworkAccess, privateEndpoints, networkDefaultAction
        """

    @staticmethod
    def get_log_analytics_workspaces() -> str:
        """Get Log Analytics Workspace details."""
        return """
        Resources
        | where type =~ 'Microsoft.OperationalInsights/workspaces'
        | extend skuName = properties.sku.name
        | extend retentionInDays = properties.retentionInDays
        | extend dailyQuotaGb = properties.workspaceCapping.dailyQuotaGb
        | project name, resourceGroup, subscriptionId, location, skuName, retentionInDays, dailyQuotaGb
        """

    # =========================================================================
    # AI & Cognitive Services Queries
    # =========================================================================

    @staticmethod
    def get_cognitive_services() -> str:
        """Get Cognitive Services / AI Services details."""
        return """
        Resources
        | where type =~ 'Microsoft.CognitiveServices/accounts'
        | extend skuName = tostring(sku.name)
        | extend skuTier = tostring(sku.tier)
        | extend publicNetworkAccess = tostring(properties.publicNetworkAccess)
        | extend customSubDomainName = tostring(properties.customSubDomainName)
        | project name, resourceGroup, subscriptionId, location, kind, skuName, skuTier,
                  publicNetworkAccess, customSubDomainName
        """

    @staticmethod
    def get_openai_deployments() -> str:
        """Get Azure OpenAI deployments (if available through Resource Graph)."""
        return """
        Resources
        | where type =~ 'Microsoft.CognitiveServices/accounts'
        | where kind =~ 'OpenAI'
        | extend skuName = sku.name
        | extend publicNetworkAccess = properties.publicNetworkAccess
        | extend endpoint = properties.endpoint
        | project name, resourceGroup, subscriptionId, location, skuName, publicNetworkAccess, endpoint
        """

    # =========================================================================
    # Advanced Analysis Queries
    # =========================================================================

    @staticmethod
    def get_resources_needing_https_upgrade() -> str:
        """Find resources that might need HTTPS/TLS upgrade."""
        return """
        Resources
        | where type =~ 'Microsoft.Web/sites' or type =~ 'Microsoft.Storage/storageAccounts'
        | extend httpsOnly = iff(type =~ 'Microsoft.Web/sites', properties.httpsOnly, properties.supportsHttpsTrafficOnly)
        | extend minTls = iff(type =~ 'Microsoft.Web/sites', properties.siteConfig.minTlsVersion, properties.minimumTlsVersion)
        | where httpsOnly == false or minTls !in ('1.2', 'TLS1_2')
        | project name, type, resourceGroup, subscriptionId, httpsOnly, minTls
        """

    @staticmethod
    def get_resources_with_public_access() -> str:
        """Find resources with public network access enabled."""
        return """
        Resources
        | where properties.publicNetworkAccess == 'Enabled' 
                or properties.allowBlobPublicAccess == true
                or properties.networkAcls.defaultAction == 'Allow'
        | project name, type, resourceGroup, subscriptionId, location,
                  publicAccess = coalesce(
                      tostring(properties.publicNetworkAccess),
                      tostring(properties.allowBlobPublicAccess),
                      tostring(properties.networkAcls.defaultAction)
                  )
        """

    # =========================================================================
    # Service-Specific Query Generator
    # =========================================================================

    @staticmethod
    def get_query_for_update_service(service_name: str) -> str:
        """Get the most appropriate detailed query for a given Azure service.

        Args:
            service_name: Name of the Azure service from the update

        Returns:
            A detailed KQL query for that service
        """
        service_lower = service_name.lower()

        if any(x in service_lower for x in ["storage", "blob", "data lake"]):
            return ResourceGraphQueryBuilder.get_storage_accounts_detail()
        elif any(x in service_lower for x in ["virtual machine", "vm", "compute"]):
            return ResourceGraphQueryBuilder.get_virtual_machines_detail()
        elif any(x in service_lower for x in ["kubernetes", "aks", "container service"]):
            return ResourceGraphQueryBuilder.get_aks_clusters_detail()
        elif any(x in service_lower for x in ["function", "serverless"]):
            return ResourceGraphQueryBuilder.get_function_apps_detail()
        elif any(x in service_lower for x in ["app service", "web app"]):
            return ResourceGraphQueryBuilder.get_app_services()
        elif any(x in service_lower for x in ["container app"]):
            return ResourceGraphQueryBuilder.get_container_apps()
        elif any(x in service_lower for x in ["sql", "database"]):
            return ResourceGraphQueryBuilder.get_sql_databases()
        elif any(x in service_lower for x in ["cosmos"]):
            return ResourceGraphQueryBuilder.get_cosmos_accounts()
        elif any(x in service_lower for x in ["key vault"]):
            return ResourceGraphQueryBuilder.get_key_vaults()
        elif any(x in service_lower for x in ["cognitive", "ai service", "openai"]):
            return ResourceGraphQueryBuilder.get_cognitive_services()
        elif any(x in service_lower for x in ["container registry", "acr"]):
            return ResourceGraphQueryBuilder.get_container_registries()
        elif any(x in service_lower for x in ["vnet", "virtual network"]):
            return ResourceGraphQueryBuilder.get_virtual_networks_detail()
        elif any(x in service_lower for x in ["nsg", "network security"]):
            return ResourceGraphQueryBuilder.get_network_security_groups_detail()
        elif any(x in service_lower for x in ["public ip"]):
            return ResourceGraphQueryBuilder.get_public_ip_addresses()
        elif any(x in service_lower for x in ["log analytics", "monitor"]):
            return ResourceGraphQueryBuilder.get_log_analytics_workspaces()
        elif any(x in service_lower for x in ["batch"]):
            return ResourceGraphQueryBuilder.list_resources_by_service("Microsoft.Batch")
        elif any(x in service_lower for x in ["api management", "apim"]):
            return ResourceGraphQueryBuilder.list_resources_by_service("Microsoft.ApiManagement")
        elif any(x in service_lower for x in ["event hub", "eventhub"]):
            return ResourceGraphQueryBuilder.list_resources_by_service("Microsoft.EventHub")
        elif any(x in service_lower for x in ["service bus", "servicebus"]):
            return ResourceGraphQueryBuilder.list_resources_by_service("Microsoft.ServiceBus")
        elif any(x in service_lower for x in ["redis", "cache"]):
            return ResourceGraphQueryBuilder.list_resources_by_service("Microsoft.Cache")
        elif any(x in service_lower for x in ["application gateway", "app gateway"]):
            return ResourceGraphQueryBuilder.list_resources_by_service(
                "Microsoft.Network/applicationGateways"
            )
        else:
            # Default: search by service name keywords
            return ResourceGraphQueryBuilder.list_resources_by_service(service_name)

    # Map of ARM resource type (lowercase) -> builder method name. Used as a
    # recovery fallback: when a custom LLM query for one of these types fails,
    # substituting the known-good builder query preserves domain projections
    # (TLS, private endpoint, public network access, ACNS, backup mode, ...)
    # instead of degrading to a generic raw-properties dump.
    _TYPE_TO_BUILDER = {
        "microsoft.storage/storageaccounts": "get_storage_accounts_detail",
        "microsoft.compute/virtualmachines": "get_virtual_machines_detail",
        "microsoft.containerservice/managedclusters": "get_aks_clusters_detail",
        "microsoft.documentdb/databaseaccounts": "get_cosmos_accounts",
        "microsoft.keyvault/vaults": "get_key_vaults",
        "microsoft.operationalinsights/workspaces": "get_log_analytics_workspaces",
        "microsoft.network/virtualnetworks": "get_virtual_networks_detail",
        "microsoft.network/networksecuritygroups": "get_network_security_groups_detail",
        "microsoft.network/publicipaddresses": "get_public_ip_addresses",
        "microsoft.containerregistry/registries": "get_container_registries",
        "microsoft.sql/servers/databases": "get_sql_databases",
        "microsoft.cognitiveservices/accounts": "get_cognitive_services",
        "microsoft.app/containerapps": "get_container_apps",
    }

    @staticmethod
    def get_query_for_resource_type(resource_type: str) -> Optional[str]:
        """Return the predefined builder query for a resource type, or None.

        Used as a recovery fallback when a custom query for this type fails: the
        known-good builder query preserves the domain-specific projections that a
        generic raw-properties dump would lose.

        Args:
            resource_type: ARM resource type (case-insensitive), e.g.
                'microsoft.storage/storageAccounts'.

        Returns:
            The builder KQL string, or None if no builder exists for the type.
        """
        method_name = ResourceGraphQueryBuilder._TYPE_TO_BUILDER.get(resource_type.strip().lower())
        if not method_name:
            return None
        return getattr(ResourceGraphQueryBuilder, method_name)()
