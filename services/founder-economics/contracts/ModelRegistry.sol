// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * Governance-controlled registry for immutable distribution-model builds.
 * A root may only cite a model hash that governance approved for that epoch.
 */
contract ModelRegistry {
    struct ModelApproval {
        uint64 activationEpoch;
        bool approved;
    }

    address public immutable governance;
    mapping(bytes32 => ModelApproval) public approvals;

    event ModelApproved(bytes32 indexed codeHash, uint256 activationEpoch);
    event ModelRevoked(bytes32 indexed codeHash);

    modifier onlyGovernance() {
        require(msg.sender == governance, "ModelRegistry: governance only");
        _;
    }

    constructor(address governance_) {
        require(governance_ != address(0), "ModelRegistry: zero governance");
        governance = governance_;
    }

    function approveModel(bytes32 codeHash, uint256 activationEpoch) external onlyGovernance {
        require(codeHash != bytes32(0), "ModelRegistry: zero code hash");
        require(activationEpoch > 0, "ModelRegistry: zero activation epoch");
        approvals[codeHash] = ModelApproval({
            activationEpoch: uint64(activationEpoch),
            approved: true
        });
        emit ModelApproved(codeHash, activationEpoch);
    }

    function revokeModel(bytes32 codeHash) external onlyGovernance {
        approvals[codeHash].approved = false;
        emit ModelRevoked(codeHash);
    }

    function isActive(bytes32 codeHash, uint256 epoch) external view returns (bool) {
        ModelApproval memory approval = approvals[codeHash];
        return approval.approved && epoch >= approval.activationEpoch;
    }
}
