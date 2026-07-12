// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * PlatformToken — ERC-20 + ERC-2612 (permit) for the Founder Economics MVP.
 *
 * Design intent (Phase 8):
 *   - Fixed total supply minted once at deploy to the deployment Safe.
 *   - No admin mint, no pause, no upgradeability — auditable & immutable.
 *   - ERC-2612 permit lets founders claim gaslessly via relayer if desired.
 *
 * This file is a DESIGN ARTIFACT for the MVP — it does not need to compile
 * without a Solidity toolchain (Foundry/Hardhat). When we wire deployment,
 * install OpenZeppelin Contracts v5 and verify with `forge build`.
 *
 * Layout:
 *   imports:  OpenZeppelin ERC20, ERC20Permit, ERC20Votes (optional governance)
 *   storage:  none beyond OZ internals
 *   ctor:     name, symbol, totalSupply, recipient (deployment Safe)
 *   fns:      none — only constructor mints; transfers are standard OZ
 */

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Votes.sol";
import "@openzeppelin/contracts/utils/Nonces.sol";

contract PlatformToken is ERC20, ERC20Permit, ERC20Votes {
    constructor(
        string memory name,
        string memory symbol,
        uint256 totalSupply,
        address recipient
    ) ERC20(name, symbol) ERC20Permit(name) {
        require(recipient != address(0), "PlatformToken: zero recipient");
        require(totalSupply > 0, "PlatformToken: zero supply");
        // Mint the entire fixed supply to the deployment Safe. Deployment
        // wiring then transfers exactly 200M to the initial DCF liquidity
        // allocation and exactly 800M to VestingVault. After this no more
        // tokens can ever be created — supply is law.
        _mint(recipient, totalSupply);
    }

    // ─── ERC20Votes hooks (required by OZ) ────────────────────────────────
    function _update(address from, address to, uint256 value) internal override(ERC20, ERC20Votes) {
        super._update(from, to, value);
    }

    function nonces(address owner)
        public
        view
        override(ERC20Permit, Nonces)
        returns (uint256)
    {
        return super.nonces(owner);
    }
}
