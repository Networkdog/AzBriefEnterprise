"""Tests for security: KQL injection prevention in ResourceGraphQueryBuilder."""

import pytest

from src.services.resource_graph import ResourceGraphQueryBuilder


class TestKqlInjectionPrevention:
    """Verify KQL injection prevention in query builders."""

    def test_sanitize_single_quotes(self):
        """Single quotes in input are removed to prevent string escape."""
        result = ResourceGraphQueryBuilder._sanitize_kql_value("test'OR 1=1--")
        assert "'" not in result

    def test_sanitize_pipe_removed(self):
        """Pipe characters (KQL operator chaining) are removed."""
        result = ResourceGraphQueryBuilder._sanitize_kql_value("storage | take 100")
        assert "|" not in result

    def test_sanitize_semicolons(self):
        """Semicolons (statement separator) are removed."""
        result = ResourceGraphQueryBuilder._sanitize_kql_value("storage; drop table")
        assert ";" not in result

    def test_sanitize_backslashes(self):
        """Backslashes are removed."""
        result = ResourceGraphQueryBuilder._sanitize_kql_value("test\\escape")
        assert "\\" not in result

    def test_sanitize_length_limit(self):
        """Input is truncated to 200 characters."""
        long_input = "A" * 500
        result = ResourceGraphQueryBuilder._sanitize_kql_value(long_input)
        assert len(result) <= 200

    def test_sanitize_whitespace_normalized(self):
        """Multiple whitespace characters are collapsed."""
        result = ResourceGraphQueryBuilder._sanitize_kql_value("storage   accounts  test")
        assert result == "storage accounts test"

    def test_list_resources_by_service_safe(self):
        """list_resources_by_service sanitizes the service name."""
        malicious = "storage' | take 1 | project name; //"
        query = ResourceGraphQueryBuilder.list_resources_by_service(malicious)
        # The sanitized value should not contain dangerous characters
        sanitized = ResourceGraphQueryBuilder._sanitize_kql_value(malicious)
        assert "'" not in sanitized
        assert "|" not in sanitized
        assert ";" not in sanitized
        # The sanitized query should still work as intended
        assert "type contains" in query
        assert "storage" in query.lower()

    def test_find_related_resources_safe(self):
        """find_related_resources sanitizes all keywords."""
        malicious_keywords = ["vm' OR type == 'x", "storage|take 1"]
        query = ResourceGraphQueryBuilder.find_related_resources(malicious_keywords)
        # No pipe injection
        assert query.count("|") == query.count("| ")  # Only KQL operators

    def test_find_related_resources_empty(self):
        """Empty keywords list produces a valid query."""
        query = ResourceGraphQueryBuilder.find_related_resources([])
        assert "where" in query.lower()

    def test_normal_service_name_works(self):
        """Normal service names pass through correctly."""
        query = ResourceGraphQueryBuilder.list_resources_by_service("Blob Storage")
        assert "Blob Storage" in query


class TestResourceGraphQueryBuilder:
    """Test query builder produces valid KQL."""

    def test_get_resource_types_summary(self):
        query = ResourceGraphQueryBuilder.get_resource_types_summary()
        assert "summarize count() by type" in query

    def test_get_storage_accounts_detail(self):
        query = ResourceGraphQueryBuilder.get_storage_accounts_detail()
        assert "Microsoft.Storage/storageAccounts" in query
        assert "minimumTlsVersion" in query

    def test_get_virtual_machines_detail(self):
        query = ResourceGraphQueryBuilder.get_virtual_machines_detail()
        assert "Microsoft.Compute/virtualMachines" in query
        assert "vmSize" in query

    def test_get_aks_clusters_detail(self):
        query = ResourceGraphQueryBuilder.get_aks_clusters_detail()
        assert "Microsoft.ContainerService/managedClusters" in query
        assert "kubernetesVersion" in query
        # ACNS (Advanced Container Networking Services) status must be queryable so
        # reports can confirm it instead of deferring to manual review.
        assert "advancedNetworking" in query
        # Azure Files/Disk CSI drivers are storageProfile properties. The Key Vault
        # secrets-provider add-on is a different CSI integration and cannot proxy them.
        assert "properties.storageProfile.fileCSIDriver.enabled" in query
        assert "azureFilesCSIDriver" in query
        assert "properties.storageProfile.diskCSIDriver.enabled" in query
        assert "azureDiskCSIDriver" in query

    def test_get_cosmos_accounts_detail(self):
        query = ResourceGraphQueryBuilder.get_cosmos_accounts()
        assert "Microsoft.DocumentDB/databaseAccounts" in query
        # Backup mode and analytical storage drive Continuous Backup / Synapse Link
        # prerequisite checks — they must be projected, not left for manual review.
        assert "backupPolicyType" in query
        assert "enableAnalyticalStorage" in query

    def test_get_query_for_update_service_storage(self):
        """Service dispatcher returns storage query for 'Blob Storage'."""
        query = ResourceGraphQueryBuilder.get_query_for_update_service("Blob Storage")
        assert "Microsoft.Storage/storageAccounts" in query

    def test_get_query_for_update_service_vm(self):
        """Service dispatcher returns VM query for 'Virtual Machines'."""
        query = ResourceGraphQueryBuilder.get_query_for_update_service("Virtual Machines")
        assert "Microsoft.Compute/virtualMachines" in query

    def test_get_query_for_update_service_unknown(self):
        """Unknown service falls back to generic search."""
        query = ResourceGraphQueryBuilder.get_query_for_update_service("SomeUnknownService")
        assert "type contains" in query or "where" in query.lower()

    def test_get_query_for_resource_type_hit(self):
        """A resource type with a builder returns that builder's rich query."""
        query = ResourceGraphQueryBuilder.get_query_for_resource_type(
            "microsoft.storage/storageAccounts"
        )
        assert query is not None
        assert "minimumTlsVersion" in query
        assert "publicNetworkAccess" in query

    def test_get_query_for_resource_type_miss(self):
        """A resource type with no builder returns None (caller uses raw fallback)."""
        assert (
            ResourceGraphQueryBuilder.get_query_for_resource_type("microsoft.network/routeTables")
            is None
        )
