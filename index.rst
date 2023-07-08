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
  Phalanx must be bootstrapped by providing a ``Secret`` object to vault-secrets-operator containing the Vault token required to read all objects below that path.

- Secrets that can be randomly generated for each Phalanx environment are created and stored in vault via the ``installer/generate_secrets.py`` script.
  The specifications for those secrets are encoded in the Python code for that script.
  The intended workflow for this script is to first read the existing secrets out of Vault using the ``installer/read_secrets.sh`` script, generate any missing secrets, and then write the secrets back into Vault using the ``installer/write_secrets.sh`` script.
  The ``installer/update_secrets.sh`` script automates this process.

- For the secrets that cannot be randomly generated for each environment, ``installer/generate_secrets.py`` supports two methods of obtaining them.
  It can prompt the user for each secret, or it can retrieve the secrets from 1Password_.
  The latter approach requires access to a `1Password Connect`_ server.
  A server for the Rubin Observatory SQuaRE team is running in the Roundtable_ Kubernetes cluster.

.. _1Password: https://1password.com/
.. _1Password Connect: https://developer.1password.com/docs/connect/
.. _Roundtable: https://roundtable.lsst.io/

- For deployments maintained by SQuaRE, the ultimate source of the secrets that cannot be randomly generated is a 1Password vault (this terminology is unfortunately confusing and has nothing to do with Vault, the service), from which the secrets are retrieved by ``installer/generate_secrets.py`` via 1Password Connect.
  The secrets are labeled with a somewhat complicated scheme to associate them with particular environments and secret names so that ``installer/generate_secrets.py`` can locate them.
  The Vault read tokens used by vault-secrets-manager for bootstrapping and ongoing secret synchronization are also stored in that 1Password vault.
