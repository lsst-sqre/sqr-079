from diagrams import Cluster, Diagram
from diagrams.gcp.compute import KubernetesEngine
from diagrams.generic.compute import Rack
from diagrams.generic.storage import Storage
from diagrams.k8s.podconfig import Secret
from diagrams.onprem.client import User
from diagrams.onprem.security import Vault
from diagrams.programming.language import Python

graph_attr = {
    "label": "",
    "labelloc": "ttc",
    "nodesep": "0.2",
    "pad": "0.2",
    "ranksep": "0.75",
    "splines": "spline",
}

node_attr = {
    "fontsize": "12.0",
}

with Diagram(
    "Proposed design",
    show=False,
    filename="proposed",
    outformat="png",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    admin = User("Administrator")

    with Cluster("Kubernetes"):
        token = Secret("Vault token")
        vso = KubernetesEngine("Vault Secrets Operator")
        secrets = Secret("Kubernetes secrets")

    with Cluster("Phalanx CLI"):
        generate = Python("phalanx generate")
        install = Python("phalanx install")

    with Cluster("Secret storage"):
        vault = Vault("Vault")
        connect = Rack("1Password Connect")
        onepassword = Storage("1Password")

    admin >> install >> token
    install << connect << onepassword
    admin >> onepassword
    admin >> generate >> vault
    generate << connect
    token >> vso >> secrets
    vso << vault
