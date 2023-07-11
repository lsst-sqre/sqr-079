:tocdepth: 1

Abstract
========

Phalanx_ is the Kubernetes management and operations layer, used to deploy both the Rubin Science Platform and other Kubernetes clusters maintained by Rubin.
As part of deploying applications on a Kubernetes cluster, secrets required by those applications must be retrieved or generated and stored in the cluster so that they are provided to running services.
This tech note discusses the requirements and possible implementation options for Phalanx secrets management and proposes a design.

.. _Phalanx: https://phalanx.lsst.io/

Starting point
==============

As of July 2023, Phalanx uses the following design for secrets management:

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

- We've gotten multiple anecdotal reports from outside SQuaRE that the Phalanx installation process is complex and hard to understand, and secrets management seems to be a significant factor in those reports.

Requirements
============

Model
-----

A Phalanx deployment consists of some number of **applications** managed by `Argo CD`.
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

- All deployments, both SQuaRE and non-SQuaRE, should use the same external secret store to ensure that the process of synchronizing secrets is tested.
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

Each application secret has a key and a description.
The former is the key in the entry in the external secret store and also normally the key in the Kubernetes ``Secret`` resource to create, and thus the key should be chosen to match the expected key used by the application's Kubernetes resources.
The description is a human-readable description of the secret and what it is used for.

Each application secret is also marked as mandatory, optional, or required if and only if a specific application setting is present.
In the last case, the setting is a Helm chart value, which may be set in either :file:`values.yaml` or in :file:`values-{environment}.yaml`.

In some cases, an application secret may be a copy of the secret used by another application.
A typical example of this is the database password used to talk to an internal PostgreSQL server deployed by Phalanx.
Both the PostgreSQL database itself and the application that talks to it must be configured with the same password.

Secrets can be divided into two major categories, static secrets and generated secrets.

Static secrets
--------------

**Static secrets** are ones that must be stored externally and cannot be automatically generated, usually because they have to be synchronized to some external system.

Because the value of the secret is taken from elsewhere, static secrets aren't associated with any additional configuration.
The one complication is that some secrets that are normally static (such as the password to an external PostgreSQL server) may instead be copied from another secret (the password configured in the internal PostgreSQL server) on some deployments.
In this case, the secret is a reference to another secret (by application and key), conditional on whether a Helm chart value is set.

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
- A bcrypt password hash.

.. _Gafaelfawr: https://gafaelfawr.lsst.io/
.. _Fernet: https://cryptography.io/en/latest/fernet/

