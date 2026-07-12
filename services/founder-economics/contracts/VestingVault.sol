// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "./IEpochDistributor.sol";

/**
 * Immutable DCF 10-year schedule.
 *
 * Supply fixture (18-decimal token units):
 * - 200M: initial DCF liquidity, funded outside this vault.
 * - 800M: this vault.
 * - 509,414,048: 40 decaying community epochs.
 * - 130,585,952: team multisig, released quarterly with each epoch.
 * - 160M plus returned expired claims: Champions terminal epoch at year ten.
 *
 * The final community release normalises harmless integer rounding so the
 * declared allocation is exact. Governance cannot alter any amount, date, or
 * recipient after deployment.
 */
contract VestingVault is ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 public constant UNIT = 1e18;
    uint256 public constant VAULT_ALLOCATION = 800_000_000 * UNIT;
    uint256 public constant COMMUNITY_ALLOCATION = 509_414_048 * UNIT;
    uint256 public constant TEAM_ALLOCATION = 130_585_952 * UNIT;
    uint256 public constant CHAMPIONS_MINIMUM = 160_000_000 * UNIT;
    uint256 public constant EPOCH_COUNT = 40;
    uint256 public constant TERMINAL_EPOCH = 41;
    uint256 public constant EPOCH_SECONDS = 90 days;
    uint256 public constant TERMINAL_DELAY = 3650 days;
    uint256 public constant COMMUNITY_EMISSION_BPS = 250;

    IERC20 public immutable token;
    IEpochDistributor public immutable distributor;
    address public immutable teamTreasury;
    uint256 public immutable startTimestamp;
    uint256 public immutable terminalTimestamp;

    uint256 public releasedEpochs;
    uint256 public communityReleased;
    uint256 public teamReleased;
    uint256 public emissionReference = VAULT_ALLOCATION;
    bool public championsReleased;

    event EpochReleased(uint256 indexed epoch, uint256 communityAmount, uint256 teamAmount);
    event ChampionsReleased(uint256 indexed epoch, uint256 amount);

    constructor(address token_, address distributor_, address teamTreasury_) {
        require(token_ != address(0), "VestingVault: zero token");
        require(distributor_ != address(0), "VestingVault: zero distributor");
        require(teamTreasury_ != address(0), "VestingVault: zero team treasury");
        token = IERC20(token_);
        distributor = IEpochDistributor(distributor_);
        teamTreasury = teamTreasury_;
        startTimestamp = block.timestamp;
        terminalTimestamp = block.timestamp + TERMINAL_DELAY;
    }

    /** Permissionless quarterly release. No administrator can skip or alter it. */
    function releaseEpoch() external nonReentrant {
        uint256 epoch = releasedEpochs + 1;
        require(epoch <= EPOCH_COUNT, "VestingVault: schedule complete");
        require(
            block.timestamp >= startTimestamp + epoch * EPOCH_SECONDS,
            "VestingVault: epoch not due"
        );
        if (releasedEpochs == 0) {
            require(token.balanceOf(address(this)) >= VAULT_ALLOCATION, "VestingVault: not funded");
        }

        uint256 communityAmount = epoch == EPOCH_COUNT
            ? COMMUNITY_ALLOCATION - communityReleased
            : (emissionReference * COMMUNITY_EMISSION_BPS) / 10_000;
        uint256 teamAmount = epoch == EPOCH_COUNT
            ? TEAM_ALLOCATION - teamReleased
            : TEAM_ALLOCATION / EPOCH_COUNT;

        emissionReference -= communityAmount;
        communityReleased += communityAmount;
        teamReleased += teamAmount;
        releasedEpochs = epoch;

        token.safeTransfer(teamTreasury, teamAmount);
        token.safeTransfer(address(distributor), communityAmount);
        distributor.fundEpoch(epoch, communityAmount);
        emit EpochReleased(epoch, communityAmount, teamAmount);
    }

    /**
     * Releases the 160M Champions reserve plus returned expired claims. The
     * distributor's terminal root still has to pass governance challenge.
     */
    function releaseChampions() external nonReentrant {
        require(!championsReleased, "VestingVault: champions already released");
        require(releasedEpochs == EPOCH_COUNT, "VestingVault: epochs incomplete");
        require(block.timestamp >= terminalTimestamp, "VestingVault: terminal not due");
        uint256 amount = token.balanceOf(address(this));
        require(amount >= CHAMPIONS_MINIMUM, "VestingVault: terminal underfunded");
        championsReleased = true;
        token.safeTransfer(address(distributor), amount);
        distributor.fundEpoch(TERMINAL_EPOCH, amount);
        emit ChampionsReleased(TERMINAL_EPOCH, amount);
    }

    function nextEpochDueAt() external view returns (uint256) {
        if (releasedEpochs >= EPOCH_COUNT) return terminalTimestamp;
        return startTimestamp + (releasedEpochs + 1) * EPOCH_SECONDS;
    }
}
