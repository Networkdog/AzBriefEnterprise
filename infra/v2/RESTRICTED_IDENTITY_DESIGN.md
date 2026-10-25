# Deployment Without App Registration, SSO, or Authentication Gateways

Status: target architecture not accepted in a customer tenant. Local initial-ingress
validation and a guarded single-entry coordinator are implemented; restricted MCP,
environment-only Admin settings and automated private acceptance/closure are not.
Decision date: 2026-10-25.
Updated decision: the customer explicitly trusts traffic from the dedicated Foundry
subnet for the tenant Azure MCP endpoint. This supersedes the earlier proposal to
remove that server and replace its specialist with native tools.

## Required Boundary

Customer deployment must not create arbitrary Entra app registrations, require SSO,
or depend on an authentication gateway. Reusing an existing SSO or gateway is not
an alternative. Giving a setup identity Graph permissions to create applications
on the customer's behalf does not satisfy this requirement either.

Deployment and application code must not create directory applications, application
service principals, passwords, app-role assignments or consent grants. Do not require
directory administration permissions or a separately approved API registration as
a hidden installation prerequisite. Azure deployment authentication and explicitly
authorized ARM RBAC assignments remain necessary; these are not application SSO.

This decision supersedes earlier automatic Entra setup recommendations for this
customer profile. The only approved application-authentication exception is the
private tenant Azure MCP endpoint after the network gates below pass. It does not
authorize public anonymous access, forged identity headers, deletion of existing
registrations, changes to current deployments, or removal of web/API authentication.

## Current Implementation Gaps

The current deployment is not compatible with this boundary:

- [MCP setup](mcp-authentication.bicep) creates an API registration and
  assigns its app role to the Foundry project identity. A deployment-only UAMI merely
  changes who performs these directory writes.
- [Initial CLI web setup](../../scripts/deploy_v2.py) creates a web registration,
  service principal and password through `_automatic_application_configuration()`.
- [Admin](../../src/admin/auth.py) and [Archive](../../src/archive/auth.py) trust EasyAuth
  identity headers; they do not currently implement local account authentication.
- The Azure MCP specialist requires a managed HTTP MCP connection and a distinct
  configured role. Its current connection and server require Entra authentication;
  network-trusted unauthenticated MCP is not a supported deployment mode yet.

Do not describe the existing Portal buttons, `--no-automatic-setup`, or an external
MCP URL as a complete solution to these constraints.

## Target Architecture

| Boundary | Target | Required protection |
| --- | --- | --- |
| Azure resource evidence | Retain distinct Resource Graph, Azure MCP and Azure API specialist ownership | Exact tenant/scope enforcement, least-privilege runtime identities, bounded requests and traceable evidence |
| Tenant Azure MCP endpoint | Retain the HTTP server; trust only the dedicated Foundry subnet without application-layer caller authentication | Private HTTPS ingress, verified source-subnet enforcement, read-only server MI, no API registration or project app-role grant |
| Microsoft Learn | Retain the public documentation transport | No tenant evidence or credentials in public documentation requests |
| Admin and Archive | Application-owned local accounts and sessions; settings may be read-only | Distinct administrator/reader authorization and per-account audit identity; not covered by the MCP exception |
| AzBrief `/mcp` | Retain the separate application control-plane API-key boundary | HTTPS, server-side constant-time validation, rotation and no keys in URLs or browser storage |
| Storage, email, telemetry and registry access | Azure runtime identities and scoped RBAC where supported | Preserve separate control-plane and analysis permissions and private networking |

## Initial Foundry Publishing Access

The requested initial inbound policy is selected virtual networks/subnets plus
explicitly approved public IPv4 addresses/CIDRs, so an authorized deployment client
can publish Agents without first obtaining a private route. The local template/CLI
now expose `foundryPublishingAccess` with `publicIpRanges` and `subnetResourceIds`.
Empty input retains private-only access. This is not an accepted live Hosted deployment.
It applies only to Foundry inbound access. Storage remains Private Endpoint-only,
and the unauthenticated tenant MCP endpoint remains private and Foundry-subnet-only.

Inbound access and outbound Agent VNet Injection are separate settings. Preserve
`networkInjections` from account creation, its exact delegated subnet, and existing
Foundry Private Endpoints/DNS. Do not switch to an uninjected Foundry account, delete
private endpoints, move a shared parent, or introduce another inference endpoint.

The candidate selected-network configuration uses `publicNetworkAccess: Enabled`
with `networkAcls.defaultAction: Deny`, an explicit `ipRules` allow-list and, when
applicable, exact `virtualNetworkRules` subnet IDs. Never use All networks or
`defaultAction: Allow`, infer a deployer's public address, substitute an Azure-wide
service tag, or enable a trusted-services bypass. Missing inputs block this mode;
they do not become unrestricted public access. Network access does not replace
Foundry authentication or project-scoped Agent publication permissions.

An inbound selected-VNet rule is not the Agent injection binding. The selected
client subnet needs the documented `Microsoft.CognitiveServices` service endpoint
where service-endpoint rules are used; clients using Private Endpoints instead
need working private routing and DNS. Do not silently add service endpoints to a
customer-owned subnet or assume the delegated Agent subnet is a client subnet.
Public IP rules use the client's actual public egress/NAT addresses, not RFC1918
addresses. Accept supported IPv4/CIDR values only, reject unrestricted ranges, and
normalize single-address and /31-/32 inputs to supported individual IP rules as
required by the service. Do not copy arbitrary Container Apps CIDR syntax unchanged.

Before production use, validate the exact AIServices account API, Basic managed-data
mode, region, Hosted Agent and private MCP combination with this inbound policy.
General Foundry/Foundry Tools firewall documentation is not proof of that complete
combination. The current private-only preflight must not simply be removed: define
a versioned, explicitly selected bootstrap policy and check the approved subnet/IP
bindings, Azure Policy, runtime connectivity and publication permissions together.
Existing/shared accounts require separate owner approval; never widen them as part
of an ordinary workload install. No live public-network change is authorized here.

Limit this exception to the approved publication window. Record the allowed sources,
owner and closing condition. After Agent publication and private-path acceptance,
the target default is to remove temporary public-IP rules and set Foundry public
network access back to Disabled, while retaining Private Endpoints and injection.
Verify that the private runtime and MCP still work and the old public source is
blocked before declaring closure complete. Failure leaves installation incomplete,
not a silently permanent exception. Network ACL rules have no assumed automatic
expiry; closing/reopening needs explicit orchestration or an operator-controlled
ARM change. Reopening for a later release requires another bounded approval.

## Foundry Subnet Trust

The accepted trust unit is the dedicated Foundry account subnet, not one project,
Prompt Agent, user or MI. All projects and agent workloads able to originate traffic
through that subnet are inside the trust boundary. A compromised permitted workload
can invoke the MCP tools with the server's Azure permissions; there is no per-agent
authentication or identity-based authorization at this MCP endpoint. Source addresses
and caller-supplied trace IDs must not be presented as authenticated agent identities.

For BYO VNet, managed MCP calls traverse the project Data Proxy in the delegated
Foundry subnet, not necessarily the Hosted Agent's direct outbound NIC. Verify actual
MCP discovery and tool-call paths before defining rules. Bind the exact approved
subnet ARM ID and current CIDR; do not pin transient worker IPs or broaden an unknown
source to a service tag, the whole VNet, peered networks, or the internet.

The target MCP ingress must remain private with public network access disabled and
HTTPS enabled. Its inbound enforcement must allow the approved Foundry subnet and
deny other client networks ahead of the default VNet allow rule. Preserve documented
Container Apps platform, health-probe and same-environment infrastructure traffic;
do not confuse those infrastructure allowances with additional client trust.

The current shared Admin/MCP Environment is not an NSG-isolated per-app boundary.
Use a dedicated internal MCP Environment/subnet, or a separately proven per-app
enforcement mechanism that also blocks shared-environment and alternate ingress
paths. NSGs cannot distinguish hostnames or URLs on a shared ingress. An external
Environment's public ingress is not controlled by its subnet NSG. If dedicated
isolation is required, disclose its additional Environment, subnet, DNS and network
resources; share existing logs/Insights where supported instead of claiming zero
resource growth. Do not add a public route or an authentication gateway as a shortcut.

Retain the MCP server's MI, scoped read-only Azure permissions, `--read-only`, and the
existing `group`, `resourcehealth`, `advisor` namespace allow-list. Resource Graph and
Azure API tools keep their own specialist boundaries. The network-trust decision
changes MCP incoming authentication only; it never removes outbound MI/RBAC checks
or authorizes mutations, broader analysis scope, or cross-specialist fallback.

Implement a distinct versioned deployment/connection policy for this mode and verify
that the pinned server and Foundry transport support it. Do not apply auth-bypass
flags to the current deployment, reinterpret missing credentials as opt-in, or mark
the MCP ready based only on a network template or successful ARM deployment. Missing,
denied, stale or ambiguous subnet/NSG evidence and any reachable untrusted path block
activation. Readiness is invalidated by a material network-binding change.

## Control-Plane Settings

Administrator-managed configuration must be changeable through Azure resource
management alone. Use Container Apps environment variables and Secrets references
as the settings source, not Blob upload/download, application API calls, or a
configuration execution Job. Reuse existing `SUBSCRIBERS`, `REPORT_LANGUAGE` and
`SCHEDULE_CRON_EXPRESSION` support and explicitly migrate remaining managed settings.
Apply common ARM parameters to both App and scheduler Job; their environment values
are not shared automatically. App updates create a new revision, while running Job
executions retain their original settings. Keep settings mutations disabled in Admin
for this mode rather than granting the app permission to rewrite its own Azure resources.
Blob remains private runtime storage for checkpoints, archives and execution leases,
not the administrator configuration source. Preserve existing saved configuration
through an explicit migration; never silently merge old Blob overrides into this mode.

Local web authentication must use a maintained authentication/password-hashing
library, not custom cryptography. Require Argon2id password hashes, individual
accounts, server-side authorization, durable revocable sessions across replicas,
Secure/HttpOnly/SameSite cookies, CSRF protection, login throttling, and credential
recovery/rotation procedures. Supply the initial administrator credential through
an operator-controlled secret channel, never a default password, deployment output,
chat, log, URL, or application image. Store no plaintext passwords. Missing account
or session storage fails closed and must never reset the administrator automatically.
Do not reuse the general machine API key as a browser administrator identity.

The MCP subnet-trust exception is not web user authentication. Do not set
`ADMIN_REQUIRE_AUTH=false` or `ARCHIVE_REQUIRE_AUTH=false` to implement this design.
Keep the web surfaces disabled until local authentication is implemented and tested.

## Platform Identity Gate

An Azure managed identity has a backing service principal. Foundry Hosted Agent
deployment also creates a platform-managed agent identity. Neither fact establishes
that the customer's directory policy permits those objects. Confirm their treatment
separately without requesting permission to create arbitrary application registrations.

Retain the Hosted Agent/control-plane separation unless an explicit architecture
change is approved. If policy prohibits the Hosted Agent's required identity, block
that deployment and document the incompatibility; do not manufacture readiness or
silently move the analyzer into the control plane. Do not claim zero new directory
objects merely because installer Graph writes have been removed.

The official Azure MCP server has local packaging options, but local transport is
not selected here as a production workaround: packaging support alone does not
establish support in this Hosted sandbox or customer production environment.

## Implementation and Acceptance

The [single-entry coordinator](../../scripts/deploy_kt_once.ps1) supplies an offline
plan, safe input-file hashes, local build/test gates, sequential existing provisioning
and publication calls, exact Hosted identity checks and fail-stop reports. It defaults
to `Plan`; `-WhatIf` performs no Azure calls. The current artifacts fail its explicit
subnet-trust/Graph-free/environment-only capability checks, so `Preflight` and `Deploy`
stop before Azure access. A selected publication window also stays blocked until
private-path acceptance and closure automation exists. No skip/force option grants
support. Synthetic supported-artifact tests do not establish real feature availability.
The coordinator never enables scheduling, sends email or declares operational readiness.

1. Version the deployment/readiness and MCP connection contracts for subnet trust.
   Remove directory setup from the selected executable template dependency graph,
   CLI, continuation links and first-install defaults. Preserve existing installations
   and their credentials; never force historical `mcp_pending` tags to `ready`.
2. Implement network-isolated MCP ingress, the explicit no-caller-auth connection,
  and updated immutable Prompt Agent versions. Keep all six roles, scoped evidence,
  query intent and action-safety gates. Verify actual Data Proxy `initialize`,
  `tools/list` and read-only tool calls succeed without application credentials;
  verify ordinary VNet, peered, operator and internet sources cannot reach the MCP.
  Test alternate/shared ingress and a failing or redirected network binding too.
3. Implement local web authentication before enabling Admin or Archive. Test missing,
   invalid and expired sessions, reader/admin separation, CSRF, spoofed EasyAuth
   headers, login limits, recovery and cross-replica session revocation.
4. Verify installation and upgrades with no installer Graph access. Require zero
   application creation, credential issuance, consent, SSO redirects or gateway
  resources. Manage settings using ARM environment/Secrets changes without operator
  Blob access, preserving App/Job consistency and runtime checkpoints/leases.
5. Verify actual platform identity policy, private connectivity, read-only evidence,
   protected UI workflows, archive and requested delivery in the customer scope.
   Compilation and local tests are not customer acceptance.
6. Validate bootstrap Foundry ingress with the exact injection/Agent profile. Test
  publication from an allowed public source and permitted private client path,
  rejection of an unlisted source, missing/invalid allow-list rejection, and the
  transition back to private-only access. Confirm Storage and MCP are never exposed
  and existing shared accounts are not widened by default.

## Official References

- [Managed identity directory objects](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/managed-identities-faq#concepts)
- [Hosted Agent identity](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents#agent-identity-and-endpoint)
- [Azure MCP deployment and usage boundaries](https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/overview)
- [Foundry Data Proxy and delegated-subnet routing](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agents-networking-deep-dive#how-traffic-flows)
- [Container Apps NSG requirements and ingress limits](https://learn.microsoft.com/en-us/azure/container-apps/firewall-integration)
- [Environment variable management](https://learn.microsoft.com/en-us/azure/container-apps/environment-variables)
- [Foundry inbound and outbound isolation](https://learn.microsoft.com/en-us/azure/foundry/how-to/configure-private-link)
- [Selected subnets and public IP rule requirements](https://learn.microsoft.com/en-us/azure/ai-services/cognitive-services-virtual-networks)