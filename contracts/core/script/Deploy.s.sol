// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import {TreasuryController} from "../src/TreasuryController.sol";

contract DeployScript is Script {
    function run() external {
        vm.startBroadcast();
        new TreasuryController();
        vm.stopBroadcast();
    }
}
