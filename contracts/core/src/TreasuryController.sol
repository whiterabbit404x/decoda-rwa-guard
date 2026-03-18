// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract TreasuryController {
    address public owner;
    uint256 public riskLimitBps = 500;

    event RiskLimitUpdated(uint256 newLimitBps);

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    function setRiskLimitBps(uint256 newLimitBps) external onlyOwner {
        require(newLimitBps <= 10_000, "invalid bps");
        riskLimitBps = newLimitBps;
        emit RiskLimitUpdated(newLimitBps);
    }
}
