# IBM Z z/VM Platform Support

This document describes the IBM Z (s390x) z/VM platform support in OCS-CI for testing OpenShift Data Foundation on IBM Z systems.

## Overview

The IBM Z z/VM support enables OCS-CI to perform node operations (reboot, shutdown, startup) on z/VM guest nodes through a bastion host. This implementation leverages existing z/VM automation scripts and cluster configuration.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    OCS-CI Framework                      │
│  ┌────────────────────────────────────────────────┐    │
│  │   IBMZZVMNodes                                  │    │
│  │   - Maps OCP node names to z/VM guests         │    │
│  │   - Calls automation scripts on bastion        │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
                          │ SSH
                          ▼
┌─────────────────────────────────────────────────────────┐
│              Bastion Host (RHEL 8.2+)                    │
│  /usr/local/bin/vmtools/cluster_config.yaml             │
│  /root/zVM_automation/AUTOMATION/                       │
│    - ZVM_BOOT_NODES_3270.py                             │
│    - ZVM_REBOOT_3270.sh                                 │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              z/VM Hypervisor                             │
│  Manages guest VMs (OCP nodes)                          │
└─────────────────────────────────────────────────────────┘
```

## Prerequisites

### Bastion Host Requirements

1. **Operating System**: RHEL 8.2 or above
2. **Packages**: `s390utils-base` (provides `vmcp` and `vmur` commands)
3. **z/VM Privileges**: Class D and G privileges
4. **Python**: Python 3 with `tessia-baselib` library
5. **Automation Scripts**: Located at `/root/zVM_automation/AUTOMATION/`
6. **Cluster Config**: `/usr/local/bin/vmtools/cluster_config.yaml`

### OCS-CI Machine Requirements

1. **SSH Access**: SSH key-based authentication to bastion host
2. **Python**: Python 3.10 or 3.11
3. **Dependencies**: `paramiko` library (included in requirements.txt)

## Configuration

### 1. Cluster Configuration (cluster_config.yaml)

The cluster configuration file on the bastion host defines all z/VM guests:

```yaml
owner:
    name: 'Your Name'
    contact: 'your.email@example.com'

nodes:
   - name: m4204003
     role: master
     zvm:
         host: boem4204
         user: m4204003
         password: password
         reader: 000c
     vnic:
         ifname: encbdf0
         id: 0.0.bdf0,0.0.bdf1,0.0.bdf2
         mac: 02:e9:05:00:00:03
         ip: 172.23.232.86
     lun:
      - id: '0x4002402000000000'
        paths:
         - wwpn: '0x500507630a1b50a4'
           fcp: 0.0.8201
```

### 2. OCS-CI Configuration

Create or use `conf/ocsci/ibmz_zvm.yaml`:

```yaml
ENV_DATA:
  platform: 'ibmz_zvm'
  deployment_type: 'upi'
  multi_arch: true
  arch: 's390x'
  
  ibmz_bastion:
    host: '172.23.232.84'  # Your bastion IP
    user: 'root'
    ssh_key: '~/.ssh/zvm-bastion-key'
  
  master_cpu: 4
  master_memory: 16384
  worker_cpu: 8
  worker_memory: 32768
  
  skip_ocp_deployment: true
  skip_ocs_deployment: false
```

### 3. SSH Key Setup

```bash
# Generate SSH key if needed
ssh-keygen -t rsa -b 4096 -f ~/.ssh/zvm-bastion-key

# Copy to bastion host
ssh-copy-id -i ~/.ssh/zvm-bastion-key root@172.23.232.84

# Test connection
ssh -i ~/.ssh/zvm-bastion-key root@172.23.232.84
```

## Node Mapping Strategy

OCS-CI maps OpenShift node names to z/VM guests using three strategies (in order):

### 1. IP Address Mapping (Primary)
```
OCP Node IP → cluster_config.yaml node.vnic.ip → z/VM guest
```
Most reliable method as it uses the actual node IP address.

### 2. Role + Index Mapping
```
master-0 → first master in config
master-1 → second master in config
worker-0 → first worker in config
```
Used when IP mapping fails. Extracts role and index from node name.

### 3. Direct Name Match
```
m4204003.example.com → m4204003 (direct match)
```
Fallback method using short hostname.

## Usage

### Running Reboot Tests

```bash
# Activate virtual environment
source .venv/bin/activate

# Run node reboot test
run-ci \
  --cluster-path ~/ibmz-cluster \
  --cluster-name m4204-cluster \
  --ocsci-conf conf/ocsci/ibmz_zvm.yaml \
  --collect-logs \
  -v -s \
  tests/functional/z_cluster/nodes/test_nodes_restart.py::TestNodesRestart::test_nodes_restart

# Run stop/start test
run-ci \
  --cluster-path ~/ibmz-cluster \
  --cluster-name m4204-cluster \
  --ocsci-conf conf/ocsci/ibmz_zvm.yaml \
  --collect-logs \
  -v -s \
  tests/functional/z_cluster/nodes/test_nodes_restart.py::TestNodesRestart::test_nodes_restart_by_stop_and_start
```

### Supported Operations

1. **Reboot Node**: `restart_nodes(nodes)`
   - Uses `ZVM_BOOT_NODES_3270.py --reboot 1`
   - Performs IPL from disk
   - Waits for node to return to Ready state

2. **Shutdown Node**: `stop_nodes(nodes)`
   - Uses `vmcp force <guest> logoff`
   - Forces guest logoff

3. **Start Node**: `start_nodes(nodes)`
   - Uses `vmcp xautolog <guest>`
   - Starts guest via autolog

4. **Stop and Start**: `restart_nodes_by_stop_and_start(nodes)`
   - Explicit shutdown followed by startup
   - 30-second delay between operations

## Implementation Details

### Key Files

- `ocs_ci/utility/ibmz.py`: ZVMBastionManager class
- `ocs_ci/ocs/platform_nodes.py`: IBMZZVMNodes class
- `conf/ocsci/ibmz_zvm.yaml`: Configuration template

### ZVMBastionManager Class

Main methods:
- `load_cluster_config()`: Reads cluster_config.yaml from bastion
- `map_ocp_node_to_zvm_guest()`: Maps OCP nodes to z/VM guests
- `reboot_node()`: Reboots a z/VM guest
- `shutdown_node()`: Shuts down a z/VM guest
- `start_node()`: Starts a z/VM guest

### IBMZZVMNodes Class

Implements the platform-specific node operations:
- `restart_nodes()`: Reboot nodes with event verification
- `stop_nodes()`: Shutdown nodes
- `start_nodes()`: Start nodes
- `restart_nodes_by_stop_and_start()`: Stop then start
- `get_reboot_events()`: Verify reboot events in Kubernetes

## Troubleshooting

### SSH Connection Issues

```bash
# Test SSH connection
ssh -i ~/.ssh/zvm-bastion-key root@172.23.232.84

# Check SSH key permissions
chmod 600 ~/.ssh/zvm-bastion-key

# Verify bastion host in config
grep -A5 ibmz_bastion conf/ocsci/ibmz_zvm.yaml
```

### Node Mapping Issues

```bash
# Check OCP node IPs
oc get nodes -o wide

# Verify cluster_config.yaml on bastion
ssh root@172.23.232.84 "cat /usr/local/bin/vmtools/cluster_config.yaml"

# Check mapping in logs
grep "Mapped.*to z/VM guest" <log_file>
```

### Automation Script Issues

```bash
# Verify scripts exist on bastion
ssh root@172.23.232.84 "ls -la /root/zVM_automation/AUTOMATION/"

# Test script manually
ssh root@172.23.232.84 "cd /root/zVM_automation/AUTOMATION && python3 ZVM_BOOT_NODES_3270.py --help"
```

### Reboot Event Not Found

If reboot events are not detected:
1. Check if node actually rebooted: `oc get nodes`
2. Verify Kubernetes events: `oc get events -A | grep Reboot`
3. Check z/VM guest status on bastion
4. Review automation script output in logs

## Limitations

1. **KVM Support**: IBM Z KVM support is not yet implemented
2. **Concurrent Operations**: Operations are sequential, not parallel
3. **Network Types**: Currently supports VSWITCH, may need updates for OSA/HiperSockets
4. **Disk Types**: Supports FCP, ECKD, and FBA (EDEV)

## Future Enhancements

1. Implement IBM Z KVM support using libvirt/virsh
2. Add parallel node operations
3. Support additional network types
4. Add node scaling operations
5. Implement node replacement functionality

## References

- [z/VM Documentation](https://www.ibm.com/docs/en/zvm)
- [OpenShift on IBM Z](https://docs.openshift.com/container-platform/latest/installing/installing_ibm_z/installing-ibm-z.html)
- [OCS-CI Documentation](https://ocs-ci.readthedocs.io/)