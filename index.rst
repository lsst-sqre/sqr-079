:tocdepth: 1

Abstract
========

Phalanx_ is the Kubernetes management and operations layer, used to deploy both the Rubin Science Platform and other Kubernetes clusters maintained by Rubin.
As part of deploying applications on a Kubernetes cluster, secrets required by those applications must be retrieved or generated and stored in the cluster so that they are provided to running services.
This tech note discusses the requirements and possible implementation options for Phalanx secrets management and proposes a design.

.. _Phalanx: https://phalanx.lsst.io/

Starting point
==============

As of August 2023, Phalanx uses the following design for secrets management:

- All applications get their secrets from Kubernetes ``Secret`` objects, either by mounting them in the file system of a pod or injecting them as environment variables.

- ``Secret`` objects are, with the exception of one bootstrapping secret discussed below and a handful of ``Secret`` objects that are implementation details of a Kubernetes application, created via ``VaultSecret`` objects.
  These objects are read by vault-secrets-operator_, which retrieves secret values from a Vault_ server and creates the corresponding ``Secret``.

.. _vault-secrets-operator: https://github.com/ricoberger/vault-secrets-operator
.. _Vault: https://www.vaultproject.io/

- Each Phalanx environment is associated with a path in a Vault server.
  All secrets for that environment are stored under that path.
  Phalanx must be bootstrapped by providing the read token for that Vault path as an argument to the :file:`installer/install.sh` script.
  It in turn creates a ``Secret`` object, which is used by vault-secrets-operator to read all objects below that path.

- Secrets that can be randomly generated for each Phalanx environment are created and stored in vault via the :file:`installer/generate_secrets.py` script.
  The specifications for those secrets are encoded in the Python code for that script.
  The intended workflow for this script is to first read the existing secrets out of Vault using the :file:`installer/read_secrets.sh` script, generate any missing secrets, and then write the secrets back into Vault using the :file:`installer/write_secrets.sh` script.
  The :file:`installer/update_secrets.sh` script automates this process.

- For the secrets that cannot be randomly generated for each environment, :file:`installer/generate_secrets.py` supports two methods of obtaining them.
  It can prompt the user for each secret, or it can retrieve the secrets from 1Password_.
  The latter approach requires access to a `1Password Connect`_ server.
  A server for the Rubin Observatory SQuaRE team is running in the Roundtable_ Kubernetes cluster.
  It provides access to a single 1Password vault that includes the secrets for all SQuaRE-run Phalanx deployments.

.. _1Password: https://1password.com/
.. _1Password Connect: https://developer.1password.com/docs/connect/
.. _Roundtable: https://roundtable.lsst.io/

- For deployments maintained by SQuaRE, the ultimate source of the secrets that cannot be randomly generated is a 1Password vault (this terminology is unfortunately confusing and has nothing to do with Vault, the service), from which the secrets are retrieved by :file:`installer/generate_secrets.py` via 1Password Connect.
  The secrets are labeled with a somewhat complicated scheme to associate them with particular environments and secret names so that :file:`installer/generate_secrets.py` can locate them.

- Vault tokens for the SQuaRE-run Vault server, used by Phalanx environments managed by SQuaRE and some other environments, are manually generated in pairs for each environment (a read token and a read/write token) using lsstvaultutils_.
  Both are then stored (manually) in the 1Password vault, and :file:`installer/update_secrets.sh` retrieves the write token directly from there.

.. _lsstvaultutils: https://github.com/lsst-sqre/lsstvaultutils/

Here is a rough diagram of the components involved in the current secrets management system.

.. diagrams:: starting.py

Problems
--------

We've run into a variety of problems with this approach.

- The process of adding a new secret feels complex and a bit awkward, and is harder to follow than manually adding the new secret directly to Vault.
  We therefore often manually update Vault secrets and then later discover they're out of sync with 1Password or with the expectations of the Phalanx installer.
  Related, adding a new secret correctly requires adding code to :file:`installer/generate_secrets.py`, but since it's easier to update Vault directly, that code is rarely tested and is often forgotten.
  It is not tested directly by Phalanx CI.

- It's difficult to get a complete picture of what secrets are required for a given Phalanx deployment.
  Some of this information is in the Argo CD configuration, some of it is encoded in the :file:`installer/generate_secrets.py` script, and some of it is based on replies to prompts from that script or from semi-magical objects stored in 1Password.

- There is no simple way to sanity-check the Vault tree for an environment for completeness, verify that the secrets that come from 1Password still match, or otherwise inspect and sanity-check the secrets management for a Phalanx environment.

- Since the write token for a given Vault path is rarely used, it often expires and is then not usable when we need it.
  (The read token is regularly refreshed by vault-secrets-operator.)

- It's not easy to audit the current Vault configuration or tokens to verify that access controls are in place as expected.

- We've gotten multiple anecdotal reports from outside SQuaRE that the Phalanx installation process is complex and hard to understand, and secrets management seems to be a significant factor in those reports.

Requirements
============

Overview of problem
-------------------

A Phalanx deployment consists of some number of **applications** managed by `Argo CD`_.
Some applications are mandatory and included in every deployment.
The rest can be enabled or disabled, depending on the needs of that deployment.

.. _Argo CD: https://argo-cd.readthedocs.io/en/stable/

Each application requires zero or more **secrets**.
For the purposes of this design, this only counts secrets that have to be managed outside of the Helm chart of the application.
Secrets that are managed internally by the application are not discussed further.

Underlying these secrets is an external secret store.
This store lives outside of the Phalanx deployment.
It may be the final and authoritative store of the necessary secrets, or it may be a copy of secrets stored in some other, more authoritative store.

These secrets are provided to applications as Kubernetes ``Secret`` resources, which (with one exception for bootstrapping) are created by retrieving the secrets from the external store.
They can be recreated as needed.

To bootstrap the deployment, and to update secrets or add new secrets as needed, the deployment must have credentials to retrieve secrets from the external secret store.
This bootstrapping secret is created directly by the Phalanx installation process as a Kubernetes ``Secret`` and is not managed like other secrets.

Design goals
------------

- SQuaRE uses 1Password as the ultimate authoritative store for persistent secrets.
  These are the secrets that cannot be randomly generated on installation because they have to be coordinated with some other, external system, such as the client secret for CILogon (see :dmtn:`224`) or the password for an external PostgreSQL server.

- All deployments, both SQuaRE and non-SQuaRE, should use the same protocol to talk to an external secret store to ensure that the process of synchronizing secrets is tested.
  That secret store should be open source software so that any Phalanx deployment can install it locally, and therefore 1Password cannot be used as that secret store.
  This means that SQuaRE deployments need to use an intermediate external secret store between 1Password and the Phalanx deployment.

- Each application should come with a definition of what secrets it requires.
  This should be specified in machine-parsable configuration, rather than in code or commentary.

- Phalanx should provide tools to manage the contents of the external secret store.
  This should include managing read credentials and write credentials for that secret store, checking that all secrets required by enabled applications are present, creating and storing any secrets that can be randomly generated for a given installation, and managing any secrets that need to be copied from 1Password for the SQuaRE use case.
  It should also be able to report any secrets that are missing or changed from 1Password, optionally updating them in the external secret store.

- Phalanx's tools should support prompting for required secrets that cannot be randomly generated, in the case where there is no authoritative secret store such as 1Password.

Secret model
============

Each Phalanx application is associated with a list of secrets that application may require.
Not all deployments of an application will require the same secrets.
Sometimes secrets are optional, and sometimes they're required only if specific application settings are present.

Each application has one and only one entry in the external secret store, named after the application.
The separate secrets for the application are stored under keys within that entry.
If the application requires those secrets to be divided into separate Kubernetes ``Secret`` resources, this should be done by the Phalanx configuration, rather than by the external secrets store, since it is an implementation detail of that application's Helm chart.

Each application secret has a key and a description.
The former is the key in the entry in the external secret store and also normally the key in the Kubernetes ``Secret`` resource to create, and thus the key should be chosen to match the expected key used by the application's Kubernetes resources.
The description is a human-readable description of the secret and what it is used for.

Each application secret is also marked as either mandatory or required only if a specific application setting is present.
In the latter case, the setting is a Helm chart value, which may be set in either :file:`values.yaml` or in :file:`values-{environment}.yaml`.
In some cases, there may be no way to determine if the secret is required based on a simple Helm chart setting, and the list of secrets may need to be configured by environment.

In some cases, an application secret may be a copy of the secret used by another application.
A typical example of this is the database password used to talk to an internal PostgreSQL server deployed by Phalanx.
Both the PostgreSQL database itself and the application that talks to it must be configured with the same password.
A secret may be a copy in some environments but not in other environments.

Secrets can be divided into three major categories, static secrets, generated secrets, and copied secrets.

Static secrets
--------------

**Static secrets** are ones that must be stored externally and cannot be automatically generated, usually because they have to be synchronized to some external system.

Because the value of the secret is taken from elsewhere, static secrets aren't associated with any additional configuration.
However, note that secrets may be static in some environments but generated or copied from other secrets for other environments, conditioned on whether certain Helm values are set.

Generated secrets
-----------------

**Generated secrets** are randomly created on installation of the deployment.
They therefore include configuration specifying how to create the secret.

Several secret generation methods are supported and can be configured:

.. rst-class:: compact

- A random alphanumeric string, used for passwords.
  32 hex digits is sufficiently long for any password and should be short enough to be accepted by any application, so this length can be used unconditionally.
- A Gafaelfawr_ token (used for a bootstrap token).
- A Fernet_ key.
- An RSA private key.
- A bcrypt password hash of another secret.

.. _Gafaelfawr: https://gafaelfawr.lsst.io/
.. _Fernet: https://cryptography.io/en/latest/fernet/

The bcrypt hash secret adds one wrinkle: the underlying secret for which it is a hash should be stored in the external secret store for human retrieval, and may be a static secret, but it should not be put into a Kubernetes ``Secret`` resource.
(The point of having a hashed secret is to avoid exposing the actual secret to Kubernetes.)

Copied secrets
--------------

**Copied secrets** occur when one application needs a copy of a secret managed by another application.
The source secret may be either static or generated (or even copied from yet another secret).

One common example is when the internal testing-only PostgreSQL server is used.
The connection password for a database must be shared between the PostgreSQL server chart and the chart of the application that uses that database.
By convention, the application owns the secret (as a generated secret) and the PostgreSQL chart copies the secret from the application.

As a special case of copied secrets, sometimes a secret should have a static, non-secret value for a given environment.
In this case, the value may be hard-coded directly into the Phalanx configuration.
This situation is rare, but arises when the application's Helm chart requires a value be provided as a secret, even though it is not secret in the context of Phalanx's configuration.

Proposed design
===============

Overview
--------

We will continue to use Vault as the external secret store and vault-secrets-operator to create corresponding Kubernetes secrets.
In the future, we will consider switching to the first-party `Vault Secrets Operator`_ released by Hashicorp.
Currently it does not support authenticating to a Vault server in a different Kubernetes cluster, which is a requirement for Phalanx.

.. _Vault Secrets Operator: https://developer.hashicorp.com/vault/tutorials/kubernetes/vault-secrets-operator

For SQuaRE deployments, we will run a single Vault server in the Roundtable cluster.
This is a non-science-platform Phalanx deployment used to run SQuaRE infrastructure.

Vault will be another Phalanx application and thus can be deployed using Phalanx, but the bootstrapping of a Phalanx deployment will assume that the Vault server is running externally.
Deployments that want to use Phalanx to manage their Vault server will therefore need to run a separate Phalanx deployment similar to Roundtable for that type of external infrastructure.
Alternately, they can deploy Vault via any other convenient local means.

Each Phalanx application that relies on provided secrets, either static or generated, will have a :file:`secrets.yaml` file at the top-level of the application chart that defines those secrets.
The specification for this YAML file is given in :ref:`secrets-spec`.
This file will describe the contents of the application's secret entry in Vault.
The mapping of those Vault keys to Kubernetes ``Secret`` resources will be specified using ``VaultSecret`` resources installed as normal by the application Helm chart.

The application may have an additional :file:`secrets-{environment}.yaml` file that specifies an additional set of secrets used only in that environment.
This usage should be rare, but is useful when the secrets are very environment-specific, such as secrets that exist only to be mounted in Notebook Aspect containers for user use.

Phalanx will provide a command-line tool to manage the secrets for a deployment written in Python.
This is described in detail under :ref:`command-line`.

This command-line tool will support importing static secrets and the read token for Vault access from 1Password.
All interactions with 1Password will be done through a 1Password Connect server.
Each deployment will have its own 1Password vault and corresponding 1Password Connect server, containing only the secrets for that deployment.
See :ref:`onepassword` for more details.

If 1Password is not in use, static secrets may be read from a local file.
See :ref:`static-secrets-file` for more details.
Alternately, the static secrets can be maintained directly in Vault, either natively or copied by a human or other non-Phalanx machinery from another source.

The existing shell-based Phalanx installer will be replaced with a new installer written in Python.
It will support creating the bootstrap secret for vault-secrets-operator, either by requiring it as a parameter or retrieving it from a 1Password Connect server.
(Other details of the new installer are outside the scope of this tech note.)

Here is a rough diagram of the proposed design.

.. diagrams:: proposed.py

This is nearly identical to the previous diagram, except the shell scripts have been replaced with the Phalanx CLI and the install operation is optionally able to read the Vault token directly from 1Password.
The 1Password links are all optional and can be omitted for deployments that do not use 1Password as the authoritative secret store, in which case static secrets will be read from a local file.

.. _secrets-spec:

Secrets specification
---------------------

Secrets for each application are specified by a file named :file:`secrets.yaml` at the top level of the application chart directory (at the same level as :file:`Chart.yaml`).
The file may be missing if the application does not need any secrets.

The top level of the file is an object mapping the key of a secret to its specification.
The key corresponds to the key under which this secret is stored in the secret entry in Vault for this application.
The entry itself will be named after the application; specifically, it matches the name of the directory under :file:`applications` in the Phalanx repository where the application chart is defined.

The specification of the secret has the following keys:

``description`` (string, required)
    Human-readable description of the secret.
    This should include a summary of what the secret is used for, any useful information about the consequences if it should be leaked, and any details on how to rotate it if needed.
    The description must be formatted with reStructuredText_.

    The ``>`` and ``|`` features of YAML may be helpful in keeping this description readable inside the YAML file.

.. _reStructuredText: https://www.sphinx-doc.org/en/master/usage/restructuredtext/basics.html

``if`` (string, optional)
    If present, specifies the conditions under which this secret is required.
    The value should be a Helm values key that, if set to a true value (including a non-empty list or object), indicates that this secret is required.
    The Phalanx tools will look first in :file:`values-{environment}.yaml` and then in :file:`values.yaml` to see if this value is set.
    If this condition evaluates to false, the secret is not used in that environment.

``copy`` (object, optional)
    If present, specifies that this secret is a copy of another secret.
    It has the following nested settings.

    ``application`` (string, required)
        Application from which to copy this secret value.

    ``key`` (string, required)
        Secret key in that application from which to copy this secret value.

    ``if`` (string, optional)
        If present, specifies a Helm values key that, if set to a true value, indicates this secret should be copied.
        It is interpreted the same as ``if`` at the top level.
        If the condition is false, the whole ``copy`` stanza will be ignored.
        If true, or if this ``if`` key is not present, either ``generate`` must be unset or must have an ``if`` condition that is false.

``generate`` (object, optional)
    Specifies that this is a generated secret rather than a static secret.
    The nested settings specify how to generate the secret.

    ``type`` (string, required)
        One of the values ``password``, ``gafaelfawr-token``, ``fernet-key``, ``rsa-private-key``, ``bcrypt-password-hash``, or ``mtime``.
        Specifies the type of generated secret.

    ``source`` (string, required for ``bcrypt-password-hash`` and ``mtime``)
        This setting is present if and only if the ``type`` is ``bcrypt-password-hash``.
        The value is the name of the key, within this application, of the secret that should be hashed to create this secret.

    ``if`` (string, optional)
        If present, specifies a Helm values key that, if set to a true value, indicates this secret should be generated.
        It is interpreted the same as ``if`` at the top level.
        If the condition is false, the whole ``generate`` stanza will be ignored (making this a static secret in that environment instead).
        If true, or if this ``if`` key is not present, either ``copy`` must be unset or must have an ``if`` condition that is false.

``value`` (string, optional)
    In some cases, applications may need a value exposed as a secret that is not actually a secret.
    The preferred way to do this is to add such values directly in the ``VaultSecret`` object, but in some cases it's clearer to store them in :file:`secrets.yaml` alongside other secrets.

    In those cases, ``value`` contains the literal value of the secret (without any encoding such as base64).
    Obviously, do not use this for any secrets that are actually secret, only for public configuration settings that have to be put into a secret due to application requirements.

    ``value`` must not be set if either ``copy`` or ``generate`` are set and either do not have an ``if`` condition or have a true ``if`` condition.

The same specification is used for both the :file:`secrets.yaml` and :file:`secrets-{environment}.yaml` files.
Either or both may be missing for a given application.
Secrets specified in :file:`secrets-{environment}.yaml` override (completely, not through merging the specifications) any secret with the same key in :file:`secrets.yaml`.

These files will be syntax-checked against a YAML schema in CI tests for the Phalanx repository.

Special secrets
---------------

There are two special secrets for each environment that fall outside of the normal per-application secret configuration.

The Vault read token for that environment will be written directly to a ``Secret`` resource in Kubernetes consumed by the vault-secrets-operator application.
This will be done as part of the installation bootstrapping process.

Each environment may have a pull secret used to authenticate Kubernetes to sources of Docker images when necessary.
The same pull secret is used by all applications in that environment that require a pull secret; per-application pull secrets are not supported in Phalanx.
This secret will be stored in Vault as ``pull-secret``, rather than under the name of any specific application, and applications can use ``VaultSecret`` resources to create a ``Secret`` from that Vault secret if they need to authenticate Docker image retrieval.

.. _vault-layout:

Vault layout
------------

Currently, SQuaRE's Vault is structured according to :dmtn:`112`.
The authentication credentials for this structure are maintained by `lsstvaultutils <https://github.com/lsst-sqre/lsstvaultutils>`__.
In practice, we haven't used Vault extensively for applications other than Kubernetes and have not needed fine-grained delegation outside of the scope of one Phalanx environment, so some of the complexity of this setup is not required.

The new design therefore uses a simpler approach.
This is also separate from the existing layout to make migration easier.

- Each environment has a tree of secrets under :samp:`phalanx/{fqdn}` (rather than :samp:`k8s_operator/{fqdn}` in the current layout).

- For vault-secrets-operator, rather than using a Vault read token (which must have an expiration time), we will use a `Vault AppRole <https://developer.hashicorp.com/vault/docs/auth/approle>`__.
  An AppRole uses a RoleID and a SecretID for authentication and does not have to have a lifetime like a Vault token.
  The AppRole will be named after the FQDN of the environment.
  (AppRoles names cannot contain the ``/`` character.)
  This will be associated with a Vault policy named :samp:`phalanx/{fqdn}/read` that grants read access to the secret tree for that environment.

- For the Phalanx command-line tool, and other write operations for an environment, we will continue to use a write token with an appropriate Vault policy.
  This will be associated with a Vault policy named :samp:`phalanx/{fqdn}/write` that grants write (including update, delete, and destroy) access to the secret tree for that environment.
  The display name of that token will be set to the FQDN of the environment.
  The write tokens will use ten year expiration times; we don't expect to make meaningful use of the expiration properties.

- Whenever creating a new read AppRole or write token for an environment, the Phalanx command-line tool will remove all existing SecretIDs for the AppRole or all other write tokens for the environment, respectively.
  The write tokens will be located by display name.

This approach will be used by the Phalanx command-line when managing Vault credentials, which will be used with the SQuaRE Vault.

This is all optional when using Phalanx.
The only requirement imposed by the Phalanx secret management tools is that all secrets for an environment be stored under one path, the secret names match the application names, vault-secrets-operator can be configured with read access to that path, and administrators have access to a write token for that path that can be set in ``VAULT_TOKEN`` when running the secret management tools.
The path and Vault server are configured in the Phalanx environment configuration, and use of the Vault credential management tools is optional.

.. _command-line:

Phalanx CLI
-----------

Phalanx will add a new command-line tool, invoked as :command:`phalanx`, that collects the various operations on a Phalanx deployment that are currently done by shell scripts in the :file:`installer` directory, as well as some new functions.

Commands relevant to this specification are divided into two subcommands: a ``secrets`` family that is useful for any Phalanx environment, and a ``vault`` family for managing Vault authentication tokens that is only relevant for environments whose Vault servers should be managed by Phalanx tools.

Secrets commands
^^^^^^^^^^^^^^^^

The general ``secrets`` commands:

:samp:`phalanx secrets audit {environment}`
    Compare the secrets required for a given environment with the secrets currently present in Vault for the given environment and report any missing or unexpected secrets.
    This command will never make changes, only report on anything unexpected.

    Pass ``--secrets`` with a path to a YAML file to provide static secrets.
    Otherwise, any static secrets in Vault will be assumed to be correct.

:samp:`phalanx secrets list {environment}`
    List all secrets required for the given environment.
    This will resolve any top-level ``if`` clauses and merge :file:`secrets-{environment}.yaml` files for that environment, but otherwise doesn't distinguish between static secrets, generated secrets, etc.

:samp:`phalanx secrets schema`
    Prints the JSON schema for :file:`secrets.yaml` to standard output.
    This is used to generate the JSON schema checked into the Phalanx repository and used by pre-commit hooks to validate the schema of the files in the tree.

:samp:`phalanx secrets static-template {environment}`
    Generate a template for the static secrets file for the given environment.
    See :ref:`static-secrets-file` for more information.

:samp:`phalanx secrets sync {environment}`
    Synchronize with Vault the secrets for the specified environment.
    This adds any missing secrets, either by generating them if they are generated secrets or obtaining them from 1Password (see :ref:`onepassword`) or from a file of static secrets (see :ref:`static-secrets-file`).
    Any already-existing secrets that are required for the environment will not be changed.

    Pass ``--secrets`` with a path to a YAML file to provide static secrets.
    Otherwise, any static secrets in Vault will be assumed to be correct.

    With the ``--delete`` flag, also delete any unexpected secrets from Vault.
    With the ``--regenerate`` flag, also change any generated secrets to newly-generated ones, invalidating the old secrets.

:samp:`phalanx secrets vault-secrets {environment} {dir}`
    Read all of the secrets for the given environment stored in Vault and write them to JSON files in the provided output directory.
    There will be one file per application, each consisting of key and value pairs holding each secret for that application that is present in Vault.
    This command is primarily useful for testing and debugging.

All of these commands except ``list`` and ``static-template`` require a Vault token for the given environment (set via the ``VAULT_TOKEN`` environment variable).
Normally the write token for that environment is used, but all the commands other than ``sync`` may be called with a read-only token.

The ``audit`` and ``sync`` commands may require a 1Password Connect authentication token if 1Password is in use (set via the ``OP_CONNECT_TOKEN`` environment variable).

Vault commands
^^^^^^^^^^^^^^

The ``vault`` commands for environments where Phalanx manages Vault credentials (all SQuaRE-run services; see :ref:`vault-layout` for more details).

:samp:`phalanx vault audit {environment}`
    Audit the credentials and configuration for the given environment.
    This checks that a read AppRole and write token exist for the environment, that both have the correct policies applied, and that the write token is not expired or about to expire.

:samp:`phalanx vault create-read-approle {environment}`
    Create the read AppRole used by vault-secrets-operator for the given environment.
    This sets up an appropriate policy to allow read access to all secrets for the given environment, creates or updates an AppRole with that policy applied, and creates a SecretID for that AppRole (and invalidates any existing SecretIDs).
    It then prints the resulting RoleID, SecretID, SecretID accessor, and other metadata to standard output as YAML.

:samp:`phalanx vault create-write-token {environment}`
    Create a write token for the given environment, suitable for using as the value of ``VAULT_TOKEN`` for subsequent ``phalanx secrets`` commands on that environment.
    This sets up an appropriate policy to allow write and delete access to all secrets for the given environment, creates a token with that policy applied, and revokes all existing write tokens for that environment.
    It then prints the new token, its accessor, and some other metadata to standard output as YAML.

All of these commands must be run with a Vault token (set via the ``VAULT_TOKEN`` environment variable) with access to create policies, AppRoles, and tokens with arbitrary policies, read token accessors, and read AppRole SecretID accessors.
Normally this will be the "provisioner" token for the Vault server.

Configuration
^^^^^^^^^^^^^

The enabled applications for a given environment will be determined from the Argo CD configuration in :file:`environments`.
Whether an optional application is enabled for an environment will be determined from the settings in :file:`values-{environment}.yaml`.

The list of mandatory applications enabled for every environment will be determined by parsing the Argo CD application definitions in :file:`environments/templates` to find Argo CD applications that are always installed.
(This approach requires more implementation work, but ensures there is no separate configuration file that can become desynchronized from the Argo CD configuration.)

Per-environment configuration required by these utilities will be set in the per-environment configuration :file:`values-{environment}.yaml` files in :file:`environments`.
Specifically, the following settings will be used:

``onePasswordConnectServer`` (optional)
    URL of the 1Password Connect server to use for this environment if this environment uses 1Password as the authoritative source for static secrets.

``vaultUrl`` (required)
    URL of the Vault server to use for this environment.

``vaultPathPrefix`` (required)
    Path within the Vault server where the secrets for this environment are stored.

Static secrets sources
----------------------

There are three options for managing the static secrets for an environment.

.. _onepassword:

1Password integration
^^^^^^^^^^^^^^^^^^^^^

Phalanx will support using 1Password directly for two things: retrieving the read token for the Vault path used for a given Phalanx environment, which in turn is provided to vault-secrets-operator so that it can synchronize Kubernetes ``Secret`` resources from Vault secrets; and retrieving the values of static secrets in order to store in Vault.
Both of these interactions are done via a `1Password Connect`_ server, which is the supported way of interacting with 1Password via an API.

1Password Connect design
""""""""""""""""""""""""

A 1Password Connect server provides access to all of the entries in a single 1Password vault.
In the initial Phalanx 1Password integration design, we used a single 1Password vault for all Phalanx environments.
This had the advantage of storing each secret only once, even if it was used by multiple environments, although the mechanism used to do that is somewhat complicated.
The drawback of this approach is access control: one Phalanx environment, including command-line invocations for that environment, should not have access to secrets for other environments for both safety and security reasons.

In this design, each Phalanx environment that uses 1Password will have its own vault.
That vault will contain only the static secrets for that environment plus the Vault read token for the Vault path for that environment.
All programmatic interactions with that 1Password Connect server will be done using the onepasswordconnectsdk_ module.

.. _onepasswordconnectsdk: https://github.com/1Password/connect-sdk-python

Each Phalanx environment 1Password vault will have a corresponding 1Password Connect server with access only to that vault.
These 1Password Connect servers will run on different URLs on the Roundtable Phalanx deployment.
(This creates a bootstrapping problem for the Roundtable environment itself, but this problem already exists since this cluster is also where the SQuaRE Vault server runs.
This cluster will have to be manually bootstrapped, outside of the abilities of the Phalanx installer.
The exact details of that bootstrap are outside the scope of this tech note.)

For each 1Password Connect server, we will generate an authentication token that provides read access to its corresponding vault.
That token will be stored in the regular SQuaRE 1Password vault, outside of the Phalanx environment vaults, where it will only be manually accessible to SQuaRE staff.
When bootstrapping or synchronizing a cluster, the SQuaRE staff member performing that work will retrieve that token and provide it to the relevant Phalanx command line invocations.

Object naming
"""""""""""""

Since all entries in a given 1Password vault are for a single Phalanx environment, and that vault is not shared with humans storing general secrets, we can use a much simpler naming convention for secrets.

The Vault read token is stored in an entry named ``vault-read-token``.

The pull secret for the environment is stored in an entry named ``pull-secret``.

All other static secrets are stored in entries named for the application.
All entries should be of type :menuselection:`Server` with all of the pre-defined sections deleted.
Each key and value pair within that entry will correspond to one secret for the application, with the key matching the key of that secret.
Fields will be marked as passwords when appropriate for their 1Password UI semantics, but Phalanx will read the secret value without regard for the type of field.

.. _static-secrets-file:

Static secrets from a file
^^^^^^^^^^^^^^^^^^^^^^^^^^

The primary alternative to using 1Password is to provide a YAML file containing values for all of the static secrets.
Phalanx has a large number of applications and a large number of static secrets, making interactive prompting unwiedy.
This approach was therefore chosen instead.

Running :samp:`phalanx secrets static-template {environment}` will generate a template for this file for a given environment.
The top level object will have one key for each application that needs static secrets.
Below each application will be one key for each static secret required by that application.
The values should be the values of the actual secrets (set to empty strings in the template).

To provide static secrets to the ``phalanx secrets audit`` and ``phalanx secrets sync`` commands, provide the ``--secrets`` flag with an argument pointing at the location of the fleshed-out YAML file with the static secret values.

Static secrets in Vault
^^^^^^^^^^^^^^^^^^^^^^^

Finally, the static secrets can be maintained directly in Vault.
If all required static secrets are already present in Vault, and no configuration is provided for any other static secrets source, the Phalanx tools will assume those static secrets are correct.

In this case, Phalanx does not care how the static secrets get into Vault.
They can be maintained there directly if desired, manually copied into Vault, copied into Vault by some other automated tool, or even automatically maintained and set by Vault via an integration if that integration can maintain the format of secret required by Phalanx.

.. _documentation:

Documentation
-------------

Similar to how documentation for the Helm chart values is automatically generated and included in the `published Phalanx documentation <https://phalanx.lsst.io/>`__, each application that uses secrets will also have an automatically-generated documentation page describing those secrets.
The description for each secret, plus any interesting configuration settings, will be extracted from :file:`secrets.yaml` or :file:`secrets-{environment}.yaml`, and the secret description page will be created using a custom Sphinx plugin.

The automatically generated documentation for environments will be enhanced to add the Vault server and documentation of whether 1Password is in use as the authoritative static secret store.
