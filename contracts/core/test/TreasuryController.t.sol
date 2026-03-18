// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {TreasuryController} from "../src/TreasuryController.sol";

contract TreasuryControllerTest is Test {
    TreasuryController internal controller;

    function setUp() public {
        controller = new TreasuryController();
    }

    function test_DefaultRiskLimit() public {
        assertEq(controller.riskLimitBps(), 500);
    }
}
