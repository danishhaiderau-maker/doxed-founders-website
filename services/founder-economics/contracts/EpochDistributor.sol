// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * EpochDistributor — permissionless Merkle-claim distributor with 365-day windows.
 *
 * Architectural rule (Founder Economics MVP):
 *   - This contract is intentionally blind: it has NO idea how a Merkle root
 *     was computed. It only stores roots per epoch and verifies proofs.
 *   - Distribution logic (pro-rata, reputation-weighted, etc.) lives off-chain.
 *     Each epoch the settlement job publishes a new root here.
 *   - Claims are permissionless: a founder submits their proof + amount and
 *     receives tokens. The contract verifies the proof against the epoch root.
 *   - Claim windows roll over 365 days after the epoch is published. After
 *     the window closes, unclaimed tokens remain in the distributor treasury
 *     for future epochs (intentional — no clawback-to-admin because there is
 *     no admin).
 *
 * DESIGN ARTIFACT — does not need to compile without Foundry/Hardhat.
 */

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract EpochDistributor is ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// Immutable token this distributor pays claims in.
    IERC20 public immutable token;
    /// Seconds after an epoch root is published during which claims are valid.
    uint256 public constant CLAIM_WINDOW_SECONDS = 365 days;

    struct Epoch {
        // Merkle root published by the off-chain settlement job.
        bytes32 root;
        // Timestamp the root was published (starts the claim window).
        uint256 publishedAt;
        // Total tokens allocated for this epoch (sum of all leaves).
        uint256 totalAllocated;
        // Total tokens actually claimed so far.
        uint256 totalClaimed;
    }

    /// epochNumber => Epoch
    mapping(uint256 => Epoch) public epochs;
    /// epochNumber => user => claimed?
    mapping(uint256 => mapping(address => bool)) public hasClaimed;

    event RootPublished(uint256 indexed epoch, bytes32 root, uint256 totalAllocated);
    event Claimed(uint256 indexed epoch, address indexed account, uint256 amount);

    constructor(address token_) {
        token = IERC20(token_);
    }

    /**
     * Publish the Merkle root for an epoch. Intended caller is the off-chain
     * settlement job (a keeper / multisig / EOA). After publish, founders can
     * claim against this root for 365 days.
     *
     * Anyone may publish a root for a not-yet-published epoch, but in practice
     * only the settlement job produces valid roots — there is no admin key to
     * steal and roots cannot be overwritten once published.
     */
    function publishRoot(
        uint256 epoch,
        bytes32 root,
        uint256 totalAllocated
    ) external {
        require(epochs[epoch].root == bytes32(0), "EpochDistributor: epoch already published");
        require(root != bytes32(0), "EpochDistributor: root cannot be zero");
        epochs[epoch] = Epoch({
            root: root,
            publishedAt: block.timestamp,
            totalAllocated: totalAllocated,
            totalClaimed: 0
        });
        emit RootPublished(epoch, root, totalAllocated);
    }

    /**
     * Claim tokens for an epoch. Permissionless — anyone with a valid proof.
     * Leaf is keccak256(abi.encode(account, amount)).
     */
    function claim(
        uint256 epoch,
        address account,
        uint256 amount,
        bytes32[] calldata proof
    ) external nonReentrant {
        Epoch storage e = epochs[epoch];
        require(e.root != bytes32(0), "EpochDistributor: epoch not published");
        require(
            block.timestamp <= e.publishedAt + CLAIM_WINDOW_SECONDS,
            "EpochDistributor: claim window closed"
        );
        require(!hasClaimed[epoch][account], "EpochDistributor: already claimed");

        bytes32 leaf = keccak256(abi.encode(account, amount));
        require(
            MerkleProof.verify(proof, e.root, leaf),
            "EpochDistributor: invalid proof"
        );

        hasClaimed[epoch][account] = true;
        e.totalClaimed += amount;
        token.safeTransfer(account, amount);
        emit Claimed(epoch, account, amount);
    }

    /// View — has the claim window for `epoch` closed?
    function claimWindowOpen(uint256 epoch) external view returns (bool) {
        Epoch storage e = epochs[epoch];
        if (e.root == bytes32(0)) return false;
        return block.timestamp <= e.publishedAt + CLAIM_WINDOW_SECONDS;
    }

    /// View — unclaimed tokens remaining for an epoch.
    function unclaimedForEpoch(uint256 epoch) external view returns (uint256) {
        Epoch storage e = epochs[epoch];
        if (e.root == bytes32(0)) return 0;
        return e.totalAllocated - e.totalClaimed;
    }
}
