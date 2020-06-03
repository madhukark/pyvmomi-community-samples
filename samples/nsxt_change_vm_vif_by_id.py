#!/usr/bin/env python
"""
Written by Madhukar Krishnarao
Github: https://github.com/madhukark

This code is released under the terms of the Apache 2
http://www.apache.org/licenses/LICENSE-2.0.html

Example script to change the network of the Virtual Machine NIC
given a NSX-T Segment ID or a NSX-T Logial Switch UUID.
Requires:
  * vSphere SDK (pyvmomi) 7.x
  * vSphere 7.x
  * NSX-T 3.x
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

    parser.add_argument('-i', '--id',
                        required=True,
                        action='store',
                        help='Segment ID or Logical Switch UUID of the NSX-T Segment')

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
        network = None
        for net in vm.runtime.host.network:
            if hasattr(net, 'config'):
                if (net.config.segmentId == args.id or
                    net.config.logicalSwitchUuid == args.id):
                    network_exists = 1
                    network = net
                    break 
            elif isinstance(net, vim.OpaqueNetwork):
                for ec in net.extraConfig:
                    # OpaqueNetworks have the Segment path in the formmat:
                    # /infra/segments/<segment-id> instead of segmentId
                    if (ec.key == "com.vmware.opaquenetwork.segment.path" and
                        args.id == ec.value.split('/')[3]):
                        network_exists = 1
                        network = net
                        break

        if not network_exists:
            print("Given network ID does not exist or invalid network type")
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
                if isinstance(network, vim.OpaqueNetwork):
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
                                        network.name),
                                        vim.dvs.DistributedVirtualPortgroup):
                    # network.config.backingType requires SDK 7.x
                    if network.config.backingType == "nsx":
                        dvs_port_connection = vim.dvs.PortConnection()
                        dvs_port_connection.portgroupKey = network.key
                        dvs_port_connection.switchUuid = \
                            network.config.distributedVirtualSwitch.uuid
                        nicspec.device.backing = \
                            vim.vm.device.VirtualEthernetCard. \
                            DistributedVirtualPortBackingInfo()
                        nicspec.device.backing.port = dvs_port_connection
                    else:
                        print("Standard PortGroups not supported")
                        return -1

                # vSphere Standard Switch
                else:
                    print("Does not support Standard Switch")
                    return -1

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
