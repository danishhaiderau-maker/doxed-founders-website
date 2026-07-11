// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * VestingVault — non-upgradeable, immutable 90-day epoch release schedule.
 *
 * Architectural rule (Founder Economics MVP):
 *   - The schedule is set at deploy and CANNOT be changed.
 *   - releaseEpoch() is PERMISSIONLESS — anyone can call it once the epoch
 *     has ended. This removes admin key risk: there is no admin.
 *   - Each release pushes a fixed 20,000,000 tokens into the EpochDistributor.
 *   - The vault has no idea who gets the tokens — that is determined off-chain
 *     by the active DistributionModel and published as a Merkle root.
 *
 * Why this matters:
 *   The on-chain layer is intentionally dumb and immutable. All upgradeable
 *   distribution logic (v1 pro-rata → v2 reputation-weighted → ...) lives
 *   off-chain and only publishes a Merkle root per epoch. Swapping the model
 *   requires zero contract changes.
 *
 * DESIGN ARTIFACT — does not need to compile without Foundry/Hardhat.
 */

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract VestingVault is ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// Immutable token released by this vault.
    IERC20 public immutable token;
    /// Immutable distributor that receives each epoch's release.
    address public immutable distributor;
    /// Tokens released per epoch (e.g. 20_000_000 * 1e18).
    uint256 public immutable releasePerEpoch;
    /// Seconds per epoch — 90 days = 7_776_000 in production.
    uint256 public immutable epochSeconds;
    /// Block time the vault was deployed (epoch 0 start).
    uint256 public immutable startTimestamp;

    /// Number of epochs released so far. Starts at 0.
    uint256 public releasedEpochs;
    /// Total tokens transferred out across all releases.
    uint256 public totalReleased;

    event EpochReleased(uint256 indexed epoch, uint256 amount, address caller);
    event ScheduleFrozen(uint256 releasePerEpoch, uint256 epochSeconds, uint256 startTimestamp);

    constructor(
        address token_,
        address distributor_,
        uint256 releasePerEpoch_,
        uint256 epochSeconds_
    ) {
        token = IERC20(token_);
        distributor = distributor_;
        releasePerEpoch = releasePerEpoch_;
        epochSeconds = epochSeconds_;
        startTimestamp = block.timestamp;
        emit ScheduleFrozen(releasePerEpoch_, epochSeconds_, startTimestamp);
    }

    /**
     * Permissionless release. Anyone may call once `block.timestamp` has passed
     * the next epoch boundary. Pulls `releasePerEpoch` from this vault into the
     * distributor. Reverts if called early or if the vault is exhausted.
     */
    function releaseEpoch() external nonReentrant {
        uint256 next = releasedEpochs + 1;
        uint256 boundary = startTimestamp + (next * epochSeconds);
        require(block.timestamp >= boundary, "VestingVault: epoch not ended");

        uint256 balance = token.balanceOf(address(this));
        require(balance >= releasePerEpoch, "VestingVault: vault exhausted");

        releasedEpochs = next;
        totalReleased += releasePerEpoch;

        token.safeTransfer(distributor, releasePerEpoch);
        emit EpochReleased(next, releasePerEpoch, msg.sender);
    }

    /// Pure helper — what epoch number are we currently inside (0-indexed)?
    function currentEpoch() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / epochSeconds;
    }

    /// Pure helper — timestamp at which `epoch` ends.
    function epochEndTime(uint256 epoch) public view returns (uint256) {
        return startTimestamp + ((epoch + 1) * epochSeconds);
    }
}
