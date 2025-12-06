"""
Protocol Handlers for MFT Application - FIXED VERSION
Implements transfer logic for all supported protocols

KEY FIX: UNCHandler now properly handles remote UNC paths
"""

import asyncio
import os
import logging
import hashlib
from typing import Dict, Any, Optional
from pathlib import Path
import paramiko
import ftplib
import urllib.request
import urllib.parse
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TransferResult:
    """Result of a transfer operation"""
    success: bool
    file_size: int
    checksum_md5: str
    checksum_sha256: str
    error_message: Optional[str] = None


class BaseProtocolHandler:
    """Base class for protocol handlers"""

    def calculate_checksums(self, file_path: str) -> Dict[str, str]:
        """Calculate MD5 and SHA256 checksums"""
        md5_hash = hashlib.md5()
        sha256_hash = hashlib.sha256()

        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    md5_hash.update(chunk)
                    sha256_hash.update(chunk)

            return {
                'checksum_md5': md5_hash.hexdigest(),
                'checksum_sha256': sha256_hash.hexdigest()
            }
        except Exception as e:
            logger.warning(f"Could not calculate checksums: {e}")
            return {
                'checksum_md5': None,
                'checksum_sha256': None
            }

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement transfer method")


class SFTPHandler(BaseProtocolHandler):
    """SFTP protocol handler"""

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file via SFTP"""
        try:
            logger.info(f"🔵 SFTP Transfer starting...")
            logger.info(f"   Source (raw): {source_path}")
            logger.info(f"   Destination (raw): {destination_path}")
            logger.info(f"   Host: {config.host}")

            # Normalize source path to Windows UNC format
            source_normalized = source_path

            # Convert forward slashes to backslashes
            if '//' in source_path or '\\\\' in source_path:
                # This is a UNC path - keep it as UNC, just normalize format
                source_normalized = source_path.replace('/', '\\')
                # Ensure it starts with exactly two backslashes
                if not source_normalized.startswith('\\\\'):
                    source_normalized = '\\\\' + source_normalized.lstrip('\\')

                # Convert C$ to C: for Windows paths
                import re
                source_normalized = re.sub(r'\\([A-Za-z])\$\\', r'\\\1:\\', source_normalized)

                logger.info(f"   UNC path normalized: {source_normalized}")
            else:
                # Convert forward slashes to backslashes for Windows
                source_normalized = source_path.replace('/', '\\')

            logger.info(f"   Source (normalized): {source_normalized}")

            # If source is a UNC path pointing to localhost, convert to local path
            if source_normalized.startswith('\\\\'):
                import socket
                # Extract host from UNC path (\\host\path)
                unc_parts = source_normalized.lstrip('\\').split('\\', 1)
                if len(unc_parts) >= 1:
                    source_host = unc_parts[0]
                    source_path_part = unc_parts[1] if len(unc_parts) > 1 else ''

                    # Get local machine IPs and hostname
                    local_ips = []
                    local_hostname = 'localhost'
                    try:
                        local_hostname = socket.gethostname()
                        local_ips = [local_hostname.lower()]
                        # Get all local IP addresses
                        for ip_info in socket.getaddrinfo(local_hostname, None):
                            ip = ip_info[4][0]
                            local_ips.append(ip)
                    except Exception as e:
                        logger.warning(f"Could not get local IPs: {e}")

                    # Check if source_host is the local machine
                    is_local = (
                        source_host.lower() in ['localhost', '127.0.0.1', local_hostname.lower()] or
                        source_host in local_ips
                    )

                    if is_local:
                        # This is a local UNC path - convert to local drive path
                        source_normalized = source_path_part
                        logger.info(f"   ✅ UNC points to localhost - converted to local: {source_normalized}")
                    else:
                        # Remote UNC path - needs authentication
                        logger.info(f"   🌐 UNC points to remote host: {source_host}")
                        logger.warning(f"   ⚠️  Remote UNC paths require the share to be accessible")
                        logger.warning(f"   ⚠️  Run this first: net use \\\\{source_host} /user:username password")

            # Create SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Connect
            connect_kwargs = {
                'hostname': config.host,
                'port': config.port,
                'username': config.username,
                'timeout': config.timeout
            }

            if config.private_key_path and os.path.exists(config.private_key_path):
                key = paramiko.RSAKey.from_private_key_file(config.private_key_path)
                connect_kwargs['pkey'] = key
            elif config.password:
                connect_kwargs['password'] = config.password

            logger.info(f"🔐 Connecting to {config.host}:{config.port} as {config.username}...")
            ssh.connect(**connect_kwargs)
            logger.info(f"✅ SSH connection established")

            # Create SFTP client
            sftp = ssh.open_sftp()

            # Ensure destination directory exists
            dest_dir = os.path.dirname(destination_path)
            if dest_dir:
                try:
                    sftp.stat(dest_dir)
                except IOError:
                    # Directory doesn't exist, create it
                    logger.info(f"📁 Creating remote directory: {dest_dir}")
                    self._mkdir_p(sftp, dest_dir)

            # Check if source exists
            if not os.path.exists(source_normalized):
                raise FileNotFoundError(f"Source file not found: {source_normalized}")

            # Check if source is a directory or file
            is_directory = os.path.isdir(source_normalized)

            if is_directory:
                # Transfer directory recursively
                logger.info(f"📁 Source is a DIRECTORY - transferring all files recursively")
                total_files = 0
                total_size = 0

                # Ensure destination directory exists
                try:
                    sftp.stat(destination_path)
                except IOError:
                    logger.info(f"📁 Creating destination directory: {destination_path}")
                    self._mkdir_p(sftp, destination_path)

                # Walk through all files in directory
                for root, dirs, files in os.walk(source_normalized):
                    # Calculate relative path from source
                    rel_path = os.path.relpath(root, source_normalized)

                    # Create remote directory structure
                    if rel_path != '.':
                        remote_dir = destination_path + '/' + rel_path.replace('\\', '/')
                    else:
                        remote_dir = destination_path

                    # Ensure remote directory exists
                    try:
                        sftp.stat(remote_dir)
                    except IOError:
                        logger.info(f"📁 Creating remote directory: {remote_dir}")
                        self._mkdir_p(sftp, remote_dir)

                    # Transfer all files in this directory
                    for filename in files:
                        local_file = os.path.join(root, filename)
                        remote_file = remote_dir + '/' + filename

                        try:
                            file_size = os.path.getsize(local_file)
                            logger.info(f"   📤 Uploading: {filename} ({file_size:,} bytes)")

                            sftp.put(local_file, remote_file)

                            # Verify
                            remote_stat = sftp.stat(remote_file)
                            if remote_stat.st_size == file_size:
                                total_files += 1
                                total_size += file_size
                                logger.info(f"      ✅ Verified: {filename}")
                            else:
                                logger.warning(f"      ⚠️ Size mismatch: {filename}")
                        except Exception as file_error:
                            logger.error(f"      ❌ Failed to upload {filename}: {file_error}")
                            # Continue with other files

                logger.info(f"✅ Directory transfer complete: {total_files} files, {total_size:,} bytes total")

                # Close connections
                sftp.close()
                ssh.close()

                return {
                    'file_size': total_size,
                    'files_transferred': total_files,
                    'checksum_md5': None,
                    'checksum_sha256': None
                }

            else:
                # Transfer single file
                logger.info(f"📄 Source is a FILE")

                # Calculate checksums before transfer
                logger.info(f"🔐 Calculating checksums...")
                checksums = self.calculate_checksums(source_normalized)
                file_size = os.path.getsize(source_normalized)
                logger.info(f"📊 File size: {file_size:,} bytes")

                # Transfer file
                logger.info(f"📤 Uploading file...")
                sftp.put(source_normalized, destination_path)

                # Verify file size
                remote_stat = sftp.stat(destination_path)
                if remote_stat.st_size != file_size:
                    raise Exception(f"File size mismatch: local={file_size}, remote={remote_stat.st_size}")

                logger.info(f"✅ Size verification: PASSED")

                # Close connections
                sftp.close()
                ssh.close()

                logger.info(f"✅ SFTP transfer successful!")

                return {
                    'file_size': file_size,
                    **checksums
                }

        except Exception as e:
            logger.error(f"❌ SFTP transfer failed: {e}")
            import traceback
            traceback.print_exc()
            raise

    def _mkdir_p(self, sftp, remote_path):
        """Create directory recursively on remote server"""
        dirs = []
        path = remote_path
        while path and path != '/':
            dirs.append(path)
            path = os.path.dirname(path)

        dirs.reverse()
        for dir_path in dirs:
            try:
                sftp.stat(dir_path)
            except IOError:
                sftp.mkdir(dir_path)


class FTPSHandler(BaseProtocolHandler):
    """FTP/FTPS protocol handler"""

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file via FTP/FTPS"""
        try:
            # Use FTP_TLS for FTPS
            if config.protocol.value in ['ftps', 'ftps']:
                ftp = ftplib.FTP_TLS()
            else:
                ftp = ftplib.FTP()

            # Connect
            ftp.connect(config.host, config.port, timeout=config.timeout)

            # Login
            if config.username and config.password:
                ftp.login(config.username, config.password)
            else:
                ftp.login()  # Anonymous

            # Switch to secure data connection for FTPS
            if config.protocol.value == 'ftps':
                ftp.prot_p()

            # Change to destination directory
            dest_dir = os.path.dirname(destination_path)
            if dest_dir:
                self._mkdir_p_ftp(ftp, dest_dir)
                ftp.cwd(dest_dir)

            # Calculate checksums
            checksums = self.calculate_checksums(source_path)
            file_size = os.path.getsize(source_path)

            # Upload file
            dest_filename = os.path.basename(destination_path)
            with open(source_path, 'rb') as f:
                ftp.storbinary(f'STOR {dest_filename}', f)

            # Verify file size
            remote_size = ftp.size(dest_filename)
            if remote_size and remote_size != file_size:
                raise Exception(f"File size mismatch: local={file_size}, remote={remote_size}")

            ftp.quit()

            logger.info(f"FTP(S) transfer successful: {source_path} -> {destination_path}")

            return {
                'file_size': file_size,
                **checksums
            }

        except Exception as e:
            logger.error(f"FTP(S) transfer failed: {e}")
            raise

    def _mkdir_p_ftp(self, ftp, remote_path):
        """Create directory recursively on FTP server"""
        dirs = []
        path = remote_path
        while path and path != '/':
            dirs.append(path)
            path = os.path.dirname(path)

        dirs.reverse()
        for dir_path in dirs:
            try:
                ftp.cwd(dir_path)
            except ftplib.error_perm:
                ftp.mkd(dir_path)
                ftp.cwd(dir_path)


class HTTPSHandler(BaseProtocolHandler):
    """HTTP/HTTPS protocol handler"""

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file via HTTP/HTTPS"""
        try:
            # Calculate checksums
            checksums = self.calculate_checksums(source_path)
            file_size = os.path.getsize(source_path)

            # Read file data
            with open(source_path, 'rb') as f:
                data = f.read()

            # Build URL
            scheme = 'https' if config.protocol.value == 'https' else 'http'
            url = f"{scheme}://{config.host}:{config.port}{destination_path}"

            # Create request
            req = urllib.request.Request(url, data=data, method='PUT')

            # Add authentication if provided
            if config.username and config.password:
                import base64
                credentials = f"{config.username}:{config.password}"
                encoded = base64.b64encode(credentials.encode()).decode()
                req.add_header('Authorization', f'Basic {encoded}')

            # Send request
            with urllib.request.urlopen(req, timeout=config.timeout) as response:
                if response.status not in [200, 201, 204]:
                    raise Exception(f"HTTP error: {response.status}")

            logger.info(f"HTTP(S) transfer successful: {source_path} -> {url}")

            return {
                'file_size': file_size,
                **checksums
            }

        except Exception as e:
            logger.error(f"HTTP(S) transfer failed: {e}")
            raise


class WebDAVHandler(BaseProtocolHandler):
    """WebDAV protocol handler"""

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file via WebDAV"""
        try:
            # WebDAV uses HTTP PUT
            checksums = self.calculate_checksums(source_path)
            file_size = os.path.getsize(source_path)

            with open(source_path, 'rb') as f:
                data = f.read()

            url = f"https://{config.host}:{config.port}{destination_path}"

            req = urllib.request.Request(url, data=data, method='PUT')

            if config.username and config.password:
                import base64
                credentials = f"{config.username}:{config.password}"
                encoded = base64.b64encode(credentials.encode()).decode()
                req.add_header('Authorization', f'Basic {encoded}')

            with urllib.request.urlopen(req, timeout=config.timeout) as response:
                if response.status not in [200, 201, 204]:
                    raise Exception(f"WebDAV error: {response.status}")

            logger.info(f"WebDAV transfer successful: {source_path} -> {url}")

            return {
                'file_size': file_size,
                **checksums
            }

        except Exception as e:
            logger.error(f"WebDAV transfer failed: {e}")
            raise


class SMBHandler(BaseProtocolHandler):
    """SMB/CIFS protocol handler"""

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file via SMB"""
        try:
            from smb.SMBConnection import SMBConnection

            # Calculate checksums
            checksums = self.calculate_checksums(source_path)
            file_size = os.path.getsize(source_path)

            # Parse UNC path: //server/share/path
            parts = destination_path.strip('/').split('/', 2)
            if len(parts) < 2:
                raise ValueError(f"Invalid SMB path: {destination_path}")

            server = parts[0]
            share = parts[1]
            remote_path = parts[2] if len(parts) > 2 else ''

            # Connect to SMB
            conn = SMBConnection(
                config.username or '',
                config.password or '',
                'mft-client',
                server,
                use_ntlm_v2=True
            )

            if not conn.connect(config.host, config.port or 445):
                raise Exception("Failed to connect to SMB server")

            # Upload file
            with open(source_path, 'rb') as f:
                conn.storeFile(share, remote_path, f)

            conn.close()

            logger.info(f"SMB transfer successful: {source_path} -> {destination_path}")

            return {
                'file_size': file_size,
                **checksums
            }

        except ImportError:
            logger.error("pysmb library not installed. Install with: pip install pysmb")
            raise
        except Exception as e:
            logger.error(f"SMB transfer failed: {e}")
            raise


class UNCHandler(BaseProtocolHandler):
    """UNC path handler (Windows network shares) - FIXED VERSION"""

    def normalize_unc_path(self, path: str, host: str = None) -> str:
        """Normalize UNC path to Windows format

        Examples:
            //192.168.1.1/C$/folder → \\\\192.168.1.1\\C$\\folder
            /C$/folder + host=192.168.1.1 → \\\\192.168.1.1\\C$\\folder
        """
        logger.info(f"Normalizing UNC path: {path} (host={host})")

        # Convert forward slashes to backslashes
        path = path.replace('/', '\\')

        # If path already starts with \\, return it
        if path.startswith('\\\\'):
            logger.info(f"  Already UNC format: {path}")
            return path

        # If host provided and path doesn't start with \\, prepend it
        if host and not path.startswith('\\\\'):
            # Fix: Calculate stripped path before f-string
            stripped_path = path.lstrip('\\')
            result = f"\\\\{host}\\{stripped_path}"
            logger.info(f"  Added host prefix: {result}")
            return result

        logger.info(f"  No changes: {path}")
        return path

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file via UNC path"""
        import shutil
        import subprocess

        logger.info(f"\n{'='*80}")
        logger.info(f"🔵 UNC TRANSFER STARTING")
        logger.info(f"{'='*80}")

        try:
            # Normalize paths to Windows UNC format
            source_normalized = self.normalize_unc_path(source_path, config.host)
            dest_normalized = self.normalize_unc_path(destination_path, config.host)

            logger.info(f"📂 Source (raw): {source_path}")
            logger.info(f"📂 Source (normalized): {source_normalized}")
            logger.info(f"📂 Dest (raw): {destination_path}")
            logger.info(f"📂 Dest (normalized): {dest_normalized}")

            # Extract hosts from UNC paths for authentication
            source_host = None
            dest_host = None

            # Extract source host if it's a UNC path
            if source_normalized.startswith('\\\\'):
                parts = source_normalized[2:].split('\\', 1)
                if parts:
                    source_host = parts[0]
                    logger.info(f"🔍 Source host extracted: {source_host}")

            # Extract destination host if it's a UNC path
            if dest_normalized.startswith('\\\\'):
                parts = dest_normalized[2:].split('\\', 1)
                if parts:
                    dest_host = parts[0]
                    logger.info(f"🔍 Dest host extracted: {dest_host}")

            # Authenticate to both source and destination if credentials provided
            if config.username and config.password:
                hosts_to_auth = set()

                # Add source host if it's remote
                if source_host:
                    hosts_to_auth.add(source_host)

                # Add destination host if it's remote
                if dest_host:
                    hosts_to_auth.add(dest_host)
                elif config.host:  # Fallback to config.host for destination
                    hosts_to_auth.add(config.host)

                # Authenticate to each unique host
                for host in hosts_to_auth:
                    unc_share = f"\\\\{host}"
                    logger.info(f"🔐 Authenticating to {unc_share} as {config.username}...")

                    try:
                        # Use net use to authenticate
                        auth_cmd = f'net use "{unc_share}" /user:{config.username} {config.password}'
                        result = subprocess.run(auth_cmd, shell=True, capture_output=True, text=True)

                        if result.returncode == 0:
                            logger.info(f"✅ Authentication successful to {host}")
                        elif "already in use" in result.stdout.lower() or "multiple connections" in result.stdout.lower():
                            logger.info(f"ℹ️  Connection already exists to {host}")
                        else:
                            logger.warning(f"⚠️  Authentication warning for {host}: {result.stdout}")

                    except Exception as auth_error:
                        logger.error(f"❌ Authentication failed to {host}: {auth_error}")
                        raise Exception(f"Failed to authenticate to {unc_share}: {auth_error}")
            else:
                logger.info(f"ℹ️  No credentials provided, using current Windows session")

            # Check if source exists
            if not os.path.exists(source_normalized):
                raise FileNotFoundError(f"Source file/directory not found: {source_normalized}")

            # Get file info
            is_dir = os.path.isdir(source_normalized)
            if is_dir:
                logger.info(f"📁 Source is a DIRECTORY")
                # For directories, calculate total size
                file_size = sum(os.path.getsize(os.path.join(dirpath, filename))
                               for dirpath, dirnames, filenames in os.walk(source_normalized)
                               for filename in filenames)
            else:
                logger.info(f"📄 Source is a FILE")
                file_size = os.path.getsize(source_normalized)

            logger.info(f"📊 File/Directory size: {file_size:,} bytes")

            # Calculate checksums (only for files)
            if not is_dir:
                try:
                    logger.info(f"🔐 Calculating checksums...")
                    checksums = self.calculate_checksums(source_normalized)
                    logger.info(f"   MD5: {checksums['checksum_md5']}")
                    logger.info(f"   SHA256: {checksums['checksum_sha256'][:16]}...")
                except Exception as e:
                    logger.warning(f"⚠️  Could not calculate checksums: {e}")
                    checksums = {'checksum_md5': None, 'checksum_sha256': None}
            else:
                checksums = {'checksum_md5': None, 'checksum_sha256': None}

            # Ensure destination directory exists
            if is_dir:
                dest_base = dest_normalized
            else:
                dest_base = os.path.dirname(dest_normalized)

            if dest_base:
                try:
                    os.makedirs(dest_base, exist_ok=True)
                    logger.info(f"📁 Created dest directory: {dest_base}")
                except Exception as e:
                    logger.warning(f"⚠️  Could not create dest dir (may already exist): {e}")

            # Copy file or directory
            logger.info(f"📤 Copying {'directory' if is_dir else 'file'}...")
            if is_dir:
                # For directories, use copytree
                if os.path.exists(dest_normalized):
                    shutil.rmtree(dest_normalized)
                shutil.copytree(source_normalized, dest_normalized)
            else:
                # For files, use copy2
                shutil.copy2(source_normalized, dest_normalized)

            # Verify
            if os.path.exists(dest_normalized):
                if is_dir:
                    dest_size = sum(os.path.getsize(os.path.join(dirpath, filename))
                                   for dirpath, dirnames, filenames in os.walk(dest_normalized)
                                   for filename in filenames)
                else:
                    dest_size = os.path.getsize(dest_normalized)

                logger.info(f"📊 Dest size: {dest_size:,} bytes")

                if dest_size == file_size:
                    logger.info(f"✅ Size verification: PASSED")
                else:
                    logger.warning(f"⚠️  Size verification: MISMATCH (source={file_size}, dest={dest_size})")

                logger.info(f"{'='*80}")
                logger.info(f"✅ UNC TRANSFER SUCCESSFUL!")
                logger.info(f"{'='*80}\n")
            else:
                raise Exception("Destination file/directory not found after copy")

            return {
                'file_size': file_size,
                **checksums
            }

        except Exception as e:
            logger.error(f"{'='*80}")
            logger.error(f"❌ UNC TRANSFER FAILED: {e}")
            logger.error(f"{'='*80}\n")
            import traceback
            traceback.print_exc()
            raise


class TFTPHandler(BaseProtocolHandler):
    """TFTP protocol handler"""

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file via TFTP"""
        try:
            import tftpy

            # Calculate checksums
            checksums = self.calculate_checksums(source_path)
            file_size = os.path.getsize(source_path)

            # Create TFTP client
            client = tftpy.TftpClient(config.host, config.port or 69)

            # Upload file
            dest_filename = os.path.basename(destination_path)
            client.upload(dest_filename, source_path, timeout=config.timeout)

            logger.info(f"TFTP transfer successful: {source_path} -> {destination_path}")

            return {
                'file_size': file_size,
                **checksums
            }

        except ImportError:
            logger.error("tftpy library not installed. Install with: pip install tftpy")
            raise
        except Exception as e:
            logger.error(f"TFTP transfer failed: {e}")
            raise


class AS2Handler(BaseProtocolHandler):
    """AS2 protocol handler"""

    async def transfer(self, source_path: str, destination_path: str, config: Any) -> Dict[str, Any]:
        """Transfer file via AS2"""
        try:
            # Calculate checksums
            checksums = self.calculate_checksums(source_path)
            file_size = os.path.getsize(source_path)

            # Read file
            with open(source_path, 'rb') as f:
                data = f.read()

            # Build URL
            url = f"https://{config.host}:{config.port}{destination_path}"

            # This is a simplified implementation
            logger.warning("AS2 handler is simplified - use dedicated AS2 library for production")

            try:
                import requests
                response = requests.post(
                    url,
                    data=data,
                    headers={
                        'AS2-From': config.username or 'mft-client',
                        'AS2-To': 'server',
                        'Content-Type': 'application/octet-stream'
                    },
                    timeout=config.timeout
                )

                if response.status_code not in [200, 201, 204]:
                    raise Exception(f"AS2 error: {response.status_code}")
            except ImportError:
                logger.error("requests library not installed. Install with: pip install requests")
                raise

            logger.info(f"AS2 transfer successful: {source_path} -> {url}")

            return {
                'file_size': file_size,
                **checksums
            }

        except Exception as e:
            logger.error(f"AS2 transfer failed: {e}")
            raise


# Export all handlers
__all__ = [
    'SFTPHandler',
    'FTPSHandler',
    'HTTPSHandler',
    'WebDAVHandler',
    'SMBHandler',
    'UNCHandler',
    'TFTPHandler',
    'AS2Handler'
]