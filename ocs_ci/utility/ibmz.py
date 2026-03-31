"""
IBM Z (s390x) platform utilities for z/VM management via bastion host

This module provides utilities for managing IBM Z z/VM guests through a bastion host.
It uses existing cluster-config.yaml and automation scripts on the bastion.
"""

import logging
import paramiko
import yaml
import re
from ocs_ci.framework import config
from ocs_ci.ocs.exceptions import CommandFailed

logger = logging.getLogger(__name__)


class ZVMBastionManager:
    """
    Manager for z/VM operations via bastion host.
    
    Uses existing cluster-config.yaml at /usr/local/bin/vmtools/cluster_config.yaml
    and automation scripts at /root/zVM_automation/AUTOMATION on the bastion host.
    """
    
    def __init__(self):
        """Initialize bastion connection parameters from config"""
        self.bastion_host = config.ENV_DATA['ibmz_bastion']['host']
        self.bastion_user = config.ENV_DATA['ibmz_bastion']['user']
        self.bastion_key = config.ENV_DATA['ibmz_bastion'].get('ssh_key')
        self.bastion_password = config.ENV_DATA['ibmz_bastion'].get('password')
        self.scripts_path = '/root/zVM_automation/AUTOMATION'
        self.cluster_config_path = '/usr/local/bin/vmtools/cluster_config.yaml'
        self._cluster_config = None
        self._node_mapping_cache = {}
        
    def _ssh_execute(self, command, timeout=300):
        """
        Execute command on bastion host via SSH
        
        Args:
            command (str): Command to execute
            timeout (int): Command timeout in seconds
            
        Returns:
            tuple: (stdout, stderr, return_code)
        """
        logger.info(f"Executing on bastion: {command}")
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            if self.bastion_key:
                ssh.connect(
                    self.bastion_host,
                    username=self.bastion_user,
                    key_filename=self.bastion_key,
                    timeout=30
                )
            else:
                ssh.connect(
                    self.bastion_host,
                    username=self.bastion_user,
                    password=self.bastion_password,
                    timeout=30
                )
            
            stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
            stdout_text = stdout.read().decode('utf-8')
            stderr_text = stderr.read().decode('utf-8')
            return_code = stdout.channel.recv_exit_status()
            
            logger.debug(f"Command output: {stdout_text}")
            if stderr_text:
                logger.debug(f"Command stderr: {stderr_text}")
            
            return stdout_text, stderr_text, return_code
            
        except Exception as e:
            logger.error(f"SSH command failed: {e}")
            raise CommandFailed(f"Failed to execute command on bastion: {e}")
        finally:
            ssh.close()
    
    def load_cluster_config(self):
        """
        Load cluster configuration from bastion host
        
        Returns:
            dict: Parsed cluster configuration
        """
        if self._cluster_config:
            return self._cluster_config
            
        logger.info(f"Loading cluster config from {self.cluster_config_path}")
        
        cmd = f"cat {self.cluster_config_path}"
        stdout, stderr, rc = self._ssh_execute(cmd)
        
        if rc != 0:
            raise CommandFailed(f"Failed to read cluster config: {stderr}")
        
        self._cluster_config = yaml.safe_load(stdout)
        logger.info(f"Loaded config for {len(self._cluster_config['nodes'])} nodes")
        return self._cluster_config
    
    def get_ocp_node_ip(self, node_obj):
        """
        Get IP address of OCP node
        
        Args:
            node_obj: OCP node object
            
        Returns:
            str: IP address of the node
        """
        node_data = node_obj.get()
        
        # Try to get internal IP
        for address in node_data['status']['addresses']:
            if address['type'] == 'InternalIP':
                return address['address']
        
        # Fallback to any IP
        for address in node_data['status']['addresses']:
            if address['type'] == 'ExternalIP':
                return address['address']
        
        raise ValueError(f"No IP address found for node {node_obj.name}")
    
    def map_ocp_node_to_zvm_guest(self, node_obj):
        """
        Map OCP node to z/VM guest using multiple strategies
        
        Strategies (in order):
        1. IP address mapping (most reliable)
        2. Role + index mapping (e.g., master-0 -> first master)
        3. Direct name match
        
        Args:
            node_obj: OCP node object
            
        Returns:
            dict: z/VM guest configuration from cluster_config.yaml
        """
        node_name = node_obj.name
        
        # Check cache first
        if node_name in self._node_mapping_cache:
            logger.info(f"Using cached mapping for {node_name}")
            return self._node_mapping_cache[node_name]
        
        config_data = self.load_cluster_config()
        
        # Strategy 1: Map by IP address (most reliable)
        try:
            node_ip = self.get_ocp_node_ip(node_obj)
            logger.info(f"Trying IP mapping for {node_name} with IP {node_ip}")
            
            for node_config in config_data['nodes']:
                if node_config.get('vnic', {}).get('ip') == node_ip:
                    logger.info(
                        f"Mapped {node_name} to z/VM guest {node_config['zvm']['user']} "
                        f"via IP {node_ip}"
                    )
                    self._node_mapping_cache[node_name] = node_config
                    return node_config
        except Exception as e:
            logger.warning(f"IP mapping failed: {e}")
        
        # Strategy 2: Map by role and index
        # Extract role and index from node name
        # Examples: master-0, worker-1, compute-2
        match = re.match(r'(master|worker|compute)-(\d+)', node_name)
        if match:
            role = match.group(1)
            index = int(match.group(2))
            
            # Map 'compute' to 'worker' for consistency
            if role == 'compute':
                role = 'worker'
            
            logger.info(f"Trying role+index mapping: {role}[{index}]")
            
            # Get all nodes with matching role
            role_nodes = [
                n for n in config_data['nodes'] 
                if n['role'] == role
            ]
            
            if index < len(role_nodes):
                node_config = role_nodes[index]
                logger.info(
                    f"Mapped {node_name} to z/VM guest {node_config['zvm']['user']} "
                    f"via role+index ({role}[{index}])"
                )
                self._node_mapping_cache[node_name] = node_config
                return node_config
        
        # Strategy 3: Direct name match (if short name matches)
        short_name = node_name.split('.')[0]
        logger.info(f"Trying direct name mapping with {short_name}")
        
        for node_config in config_data['nodes']:
            if node_config['name'] == short_name:
                logger.info(
                    f"Mapped {node_name} to z/VM guest {node_config['zvm']['user']} "
                    f"via direct name match"
                )
                self._node_mapping_cache[node_name] = node_config
                return node_config
        
        raise ValueError(
            f"Could not map OCP node {node_name} to any z/VM guest. "
            f"Tried IP, role+index, and direct name matching."
        )
    
    def shutdown_node(self, node_obj):
        """
        Shutdown a z/VM guest node
        
        Args:
            node_obj: OCP node object
            
        Returns:
            bool: True if shutdown successful
        """
        node_config = self.map_ocp_node_to_zvm_guest(node_obj)
        zvm_user = node_config['zvm']['user']
        
        logger.info(f"Shutting down z/VM guest: {zvm_user}")
        
        # Use vmcp to force logoff the guest
        cmd = f"vmcp force {zvm_user} logoff"
        stdout, stderr, rc = self._ssh_execute(cmd, timeout=120)
        
        if rc != 0:
            logger.error(f"Shutdown failed for {zvm_user}: {stderr}")
            raise CommandFailed(f"Failed to shutdown {zvm_user}: {stderr}")
        
        logger.info(f"Successfully shut down {zvm_user}")
        return True
    
    def start_node(self, node_obj):
        """
        Start a z/VM guest node
        
        Args:
            node_obj: OCP node object
            
        Returns:
            bool: True if start successful
        """
        node_config = self.map_ocp_node_to_zvm_guest(node_obj)
        zvm_user = node_config['zvm']['user']
        
        logger.info(f"Starting z/VM guest: {zvm_user}")
        
        # Use vmcp to autolog the guest
        cmd = f"vmcp xautolog {zvm_user}"
        stdout, stderr, rc = self._ssh_execute(cmd, timeout=120)
        
        if rc != 0:
            logger.error(f"Start failed for {zvm_user}: {stderr}")
            raise CommandFailed(f"Failed to start {zvm_user}: {stderr}")
        
        logger.info(f"Successfully started {zvm_user}")
        return True
    
    def reboot_node(self, node_obj):
        """
        Reboot a z/VM guest node using automation script
        
        Args:
            node_obj: OCP node object
            
        Returns:
            bool: True if reboot successful
        """
        node_config = self.map_ocp_node_to_zvm_guest(node_obj)
        zvm_user = node_config['zvm']['user']
        zvm_host = node_config['zvm']['host']
        zvm_pass = node_config['zvm']['password']
        
        logger.info(f"Rebooting z/VM guest: {zvm_user}")
        
        # Build disk configuration
        disk_args = []
        if 'lun' in node_config:
            # FCP disk
            for lun in node_config['lun']:
                lun_id = lun['id'][2:]  # Remove '0x' prefix
                for path in lun['paths']:
                    fcp_dev = path['fcp']
                    wwpn = path['wwpn'][2:]  # Remove '0x' prefix
                    disk_args.extend([
                        f"--disk_dev {fcp_dev}",
                        f"--fcp_rport {wwpn}",
                        f"--fcp_lun {lun_id}"
                    ])
            disk_type = "FCP"
        elif 'eckd' in node_config:
            # ECKD disk
            disk_dev = node_config['eckd']['id']
            disk_args.append(f"--disk_dev {disk_dev}")
            disk_type = "ECKD"
        elif 'fba' in node_config:
            # FBA disk
            disk_dev = node_config['fba']['id']
            disk_args.append(f"--disk_dev {disk_dev}")
            disk_type = "EDEV"
        else:
            raise ValueError(f"No disk configuration found for {zvm_user}")
        
        # Build network configuration
        net_type = "VSWITCH"  # Default
        net_dev = node_config['vnic']['id']
        
        # Get CPU and memory from config or use defaults
        cpu = config.ENV_DATA.get('master_cpu', 4) if node_config['role'] == 'master' else config.ENV_DATA.get('worker_cpu', 8)
        memory = config.ENV_DATA.get('master_memory', 16384) if node_config['role'] == 'master' else config.ENV_DATA.get('worker_memory', 32768)
        
        # Execute reboot via Python script
        cmd = f"""
        cd {self.scripts_path} && \
        python3 ZVM_BOOT_NODES_3270.py \
            --zvmname {zvm_user} \
            --zvmhost {zvm_host} \
            --zvmuser {zvm_user} \
            --zvmpass {zvm_pass} \
            --cpu {cpu} \
            --memory {memory} \
            --disk_type {disk_type} \
            {' '.join(disk_args)} \
            --net_type "{net_type}" \
            --net_dev "{net_dev}" \
            --reboot 1
        """
        
        stdout, stderr, rc = self._ssh_execute(cmd, timeout=600)
        
        if rc != 0:
            logger.error(f"Reboot failed for {zvm_user}: {stderr}")
            raise CommandFailed(f"Failed to reboot {zvm_user}: {stderr}")
        
        logger.info(f"Successfully initiated reboot for {zvm_user}")
        return True

# Made with Bob
