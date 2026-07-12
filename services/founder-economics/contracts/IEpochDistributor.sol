// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IEpochDistributor {
    function fundEpoch(uint256 epoch, uint256 amount) external;
}
