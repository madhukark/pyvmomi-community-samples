#!/usr/bin/env python
"""
Written by Yasen Simeonov
Github: https://github.com/yasensim

Addition by Madhukar Krishnarao
Github: https://github.com/madhukark

This code is released under the terms of the Apache 2
http://www.apache.org/licenses/LICENSE-2.0.html

Example script to change the network of the Virtual Machine NIC
that includes NSX-T opeque switch
Addition:
  - Add support for dealing with mutiple DVPG names. Can happen when the
    following cases are met
      - NSX 3.0+ vSphere 7.0+ and
      - Multiple Clusters, each with its own VDS prepped for NSX and
      - all the clusters are part of the same Transport Zone
    In such a case, each NSX Segment will have a NSX DVPG under each of the VDS
################################################################################
"""

import atexit
import ssl

from tools import cli
from tools import tasks
from pyVim import connect
from pyVmomi import vim, vmodl


def get_args():
    """Get command line args from the user.
    """
    parser = cli.build_arg_parser()

    parser.add_argument('-v', '--vm_name',
                        required=True,
                        action='store',
                        help='Virtual machine name')

    parser.add_argument('-n', '--network_name',
                        required=True,
                        action='store',
                        help='Name of the portgroup or NSX-T Segment')

    args = parser.parse_args()

    cli.prompt_for_password(args)
    return args


def get_obj(content, vimtype, name):
    """
     Get the vsphere object associated with a given text name
    """
    obj = None
    container = content.viewManager.CreateContainerView(content.rootFolder,
                                                        vimtype, True)
    for view in container.view:
        if view.name == name:
            obj = view
            break
    return obj


def main():
    """
    Simple command-line program for changing network virtual machines NIC
    that includes NSX-T opeque switch.
    """

    args = get_args()
    host_nsx_dvpgs = []
    sslContext = None

    if args.disable_ssl_verification:
        sslContext = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        sslContext.verify_mode = ssl.CERT_NONE

    try:
        service_instance = connect.SmartConnect(host=args.host,
                                                user=args.user,
                                                pwd=args.password,
                                                port=int(args.port),
                                                sslContext=sslContext)
        if not service_instance:
            print("Could not connect to the specified host using specified "
                  "username and password")
            return -1

        atexit.register(connect.Disconnect, service_instance)
        content = service_instance.RetrieveContent()
        vm = get_obj(content, [vim.VirtualMachine], args.vm_name)
        network_exists = 0
        for network in vm.runtime.host.network:
            if (network.name == args.network_name):
                network_exists = 1
            if isinstance(get_obj(content,
                                  [vim.Network],
                                  network.name), vim.DistributedVirtualPortgroup):
                if (network.config.backingType == "nsx" and
                    network.name == args.network_name):
                    host_nsx_dvpgs.append(network)
        if not network_exists:
            print("Given network " + args.network_name + " is not avaiable on the host")
            return -1
        if len(host_nsx_dvpgs) > 1:
            print("Multiple NSX-T Segments of the same name found. Cannot pick the "
                  "correct one based on the network name.")
            print("Available Networks on the host with the name: " + args.network_name)
            for nsx_dvpg in host_nsx_dvpgs:
                print(" network.config.segmentId: %s, " \
                      " network.config.logicalSwitchUuid: %s" % \
                    (nsx_dvpg.config.segmentId, nsx_dvpg.config.logicalSwitchUuid))
            return -1
        # This code is for changing only one Interface. For multiple Interface
        # Iterate through a loop of network names.
        device_change = []
        for device in vm.config.hardware.device:
            if isinstance(device, vim.vm.device.VirtualEthernetCard):
                nicspec = vim.vm.device.VirtualDeviceSpec()
                nicspec.operation = \
                    vim.vm.device.VirtualDeviceSpec.Operation.edit
                nicspec.device = device
                nicspec.device.wakeOnLanEnabled = True

                # NSX-T Logical Switch
                if isinstance(get_obj(content,
                                      [vim.Network],
                                      args.network_name), vim.OpaqueNetwork):
                    network = \
                        get_obj(content, [vim.Network], args.network_name)
           # Check and see if we need to validate for duplicate OpaqueNetworks
                    nicspec.device.backing = \
                        vim.vm.device.VirtualEthernetCard. \
                        OpaqueNetworkBackingInfo()
                    network_id = network.summary.opaqueNetworkId
                    network_type = network.summary.opaqueNetworkType
                    nicspec.device.backing.opaqueNetworkType = network_type
                    nicspec.device.backing.opaqueNetworkId = network_id

                # vSphere Distributed Virtual Switch
                elif isinstance(get_obj(content,
                                        [vim.Network],
                                        args.network_name),
                                        vim.dvs.DistributedVirtualPortgroup):
                    network = get_obj(content,
                                      [vim.dvs.DistributedVirtualPortgroup],
                                      args.network_name)
                    if network.config.backingType == "nsx":
                        # This is a NSX DVPG. Make sure its the one available on the host
                        found = 0
                        for nsx_dvpg in host_nsx_dvpgs:
                            if nsx_dvpg.name == network.name:
                                network = nsx_dvpg
                                found = found + 1

                    dvs_port_connection = vim.dvs.PortConnection()
                    dvs_port_connection.portgroupKey = network.key
                    dvs_port_connection.switchUuid = \
                        network.config.distributedVirtualSwitch.uuid
                    nicspec.device.backing = \
                        vim.vm.device.VirtualEthernetCard. \
                        DistributedVirtualPortBackingInfo()
                    nicspec.device.backing.port = dvs_port_connection

                # vSphere Standard Switch
                else:
                    nicspec.device.backing = \
                        vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
                    nicspec.device.backing.network = \
                        get_obj(content, [vim.Network], args.network_name)
                    nicspec.device.backing.deviceName = args.network_name

                nicspec.device.connectable = \
                    vim.vm.device.VirtualDevice.ConnectInfo()
                nicspec.device.connectable.startConnected = True
                nicspec.device.connectable.allowGuestControl = True
                nicspec.device.connectable.connected = True
                device_change.append(nicspec)
                break

        config_spec = vim.vm.ConfigSpec(deviceChange=device_change)
        task = vm.ReconfigVM_Task(config_spec)
        tasks.wait_for_tasks(service_instance, [task])
        print("Successfully changed network")

    except vmodl.MethodFault as error:
        print("Caught vmodl fault : " + error.msg)
        return -1

    return 0


# Start program
if __name__ == "__main__":
    main()
