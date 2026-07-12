// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "./ModelRegistry.sol";

/**
 * Funded, governance-challenged Merkle distributor.
 * Roots are never public-write: a governance-approved keeper proposes them,
 * the public has seven days to inspect the IPFS data, and governance may veto
 * before finalisation. Claims use keccak256(abi.encode(account, amount)).
 */
contract EpochDistributor is ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum Status { NONE, FUNDED, PROPOSED, FINALIZED, EXPIRED }

    struct Epoch {
        uint256 funded;
        uint256 totalClaimed;
        bytes32 root;
        bytes32 modelCodeHash;
        bytes32 proofDataHash;
        uint64 proposedAt;
        uint64 finalizedAt;
        Status status;
    }

    uint256 public constant CHALLENGE_WINDOW = 7 days;
    uint256 public constant CLAIM_WINDOW = 365 days;

    IERC20 public immutable token;
    ModelRegistry public immutable modelRegistry;
    address public immutable governance;
    address public keeper;
    address public vestingVault;
    uint256 public totalReserved;

    mapping(uint256 => Epoch) public epochs;
    mapping(uint256 => mapping(address => bool)) public hasClaimed;

    event KeeperUpdated(address indexed keeper);
    event VestingVaultSet(address indexed vestingVault);
    event EpochFunded(uint256 indexed epoch, uint256 amount);
    event RootProposed(uint256 indexed epoch, bytes32 root, bytes32 modelCodeHash, bytes32 proofDataHash);
    event RootVetoed(uint256 indexed epoch);
    event RootFinalized(uint256 indexed epoch);
    event Claimed(uint256 indexed epoch, address indexed account, uint256 amount);
    event ExpiredFundsReturned(uint256 indexed epoch, uint256 amount);

    modifier onlyGovernance() {
        require(msg.sender == governance, "EpochDistributor: governance only");
        _;
    }

    modifier onlyKeeper() {
        require(msg.sender == keeper, "EpochDistributor: keeper only");
        _;
    }

    modifier onlyVestingVault() {
        require(msg.sender == vestingVault, "EpochDistributor: vault only");
        _;
    }

    constructor(address token_, address governance_, address modelRegistry_) {
        require(token_ != address(0), "EpochDistributor: zero token");
        require(governance_ != address(0), "EpochDistributor: zero governance");
        require(modelRegistry_ != address(0), "EpochDistributor: zero registry");
        token = IERC20(token_);
        governance = governance_;
        modelRegistry = ModelRegistry(modelRegistry_);
    }

    /** One-time wiring after the vault is deployed; it can never be changed. */
    function setVestingVault(address vestingVault_) external onlyGovernance {
        require(vestingVault == address(0), "EpochDistributor: vault already set");
        require(vestingVault_ != address(0), "EpochDistributor: zero vault");
        vestingVault = vestingVault_;
        emit VestingVaultSet(vestingVault_);
    }

    function setKeeper(address keeper_) external onlyGovernance {
        require(keeper_ != address(0), "EpochDistributor: zero keeper");
        keeper = keeper_;
        emit KeeperUpdated(keeper_);
    }

    /** Called only after the vault has transferred the exact epoch allocation. */
    function fundEpoch(uint256 epoch, uint256 amount) external onlyVestingVault {
        require(amount > 0, "EpochDistributor: zero funding");
        Epoch storage e = epochs[epoch];
        require(e.status == Status.NONE, "EpochDistributor: epoch funded");
        require(token.balanceOf(address(this)) >= totalReserved + amount, "EpochDistributor: unfunded");
        e.funded = amount;
        e.status = Status.FUNDED;
        totalReserved += amount;
        emit EpochFunded(epoch, amount);
    }

    function proposeRoot(
        uint256 epoch,
        bytes32 root,
        uint256 totalAllocated,
        bytes32 modelCodeHash,
        bytes32 proofDataHash
    ) external onlyKeeper {
        Epoch storage e = epochs[epoch];
        require(e.status == Status.FUNDED, "EpochDistributor: epoch not fundable");
        require(root != bytes32(0), "EpochDistributor: zero root");
        require(proofDataHash != bytes32(0), "EpochDistributor: missing proof data");
        require(totalAllocated == e.funded, "EpochDistributor: allocation must equal funding");
        require(modelRegistry.isActive(modelCodeHash, epoch), "EpochDistributor: model not approved");
        e.root = root;
        e.modelCodeHash = modelCodeHash;
        e.proofDataHash = proofDataHash;
        e.proposedAt = uint64(block.timestamp);
        e.status = Status.PROPOSED;
        emit RootProposed(epoch, root, modelCodeHash, proofDataHash);
    }

    /** Governance can stop a fraudulent or malformed root during challenge. */
    function vetoRoot(uint256 epoch) external onlyGovernance {
        Epoch storage e = epochs[epoch];
        require(e.status == Status.PROPOSED, "EpochDistributor: root not proposed");
        require(block.timestamp < e.proposedAt + CHALLENGE_WINDOW, "EpochDistributor: challenge ended");
        e.root = bytes32(0);
        e.modelCodeHash = bytes32(0);
        e.proofDataHash = bytes32(0);
        e.proposedAt = 0;
        e.status = Status.FUNDED;
        emit RootVetoed(epoch);
    }

    function finalizeRoot(uint256 epoch) external {
        Epoch storage e = epochs[epoch];
        require(e.status == Status.PROPOSED, "EpochDistributor: root not proposed");
        require(block.timestamp >= e.proposedAt + CHALLENGE_WINDOW, "EpochDistributor: challenge open");
        e.finalizedAt = uint64(block.timestamp);
        e.status = Status.FINALIZED;
        emit RootFinalized(epoch);
    }

    function claim(uint256 epoch, uint256 amount, bytes32[] calldata proof) external nonReentrant {
        Epoch storage e = epochs[epoch];
        require(e.status == Status.FINALIZED, "EpochDistributor: root not final");
        require(block.timestamp <= e.finalizedAt + CLAIM_WINDOW, "EpochDistributor: claim window closed");
        require(!hasClaimed[epoch][msg.sender], "EpochDistributor: already claimed");
        bytes32 leaf = keccak256(abi.encode(msg.sender, amount));
        require(MerkleProof.verify(proof, e.root, leaf), "EpochDistributor: invalid proof");
        hasClaimed[epoch][msg.sender] = true;
        e.totalClaimed += amount;
        totalReserved -= amount;
        token.safeTransfer(msg.sender, amount);
        emit Claimed(epoch, msg.sender, amount);
    }

    /** Permissionless return of expired claims to the terminal vault. */
    function returnExpiredFunds(uint256 epoch) external nonReentrant {
        Epoch storage e = epochs[epoch];
        require(e.status == Status.FINALIZED, "EpochDistributor: root not final");
        require(block.timestamp > e.finalizedAt + CLAIM_WINDOW, "EpochDistributor: claim window open");
        uint256 unclaimed = e.funded - e.totalClaimed;
        e.status = Status.EXPIRED;
        totalReserved -= unclaimed;
        token.safeTransfer(vestingVault, unclaimed);
        emit ExpiredFundsReturned(epoch, unclaimed);
    }

    function challengeEndsAt(uint256 epoch) external view returns (uint256) {
        return epochs[epoch].proposedAt + CHALLENGE_WINDOW;
    }

    function claimEndsAt(uint256 epoch) external view returns (uint256) {
        return epochs[epoch].finalizedAt + CLAIM_WINDOW;
    }
}
