// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * PlatformToken — ERC-20 + ERC-2612 (permit) for the Founder Economics MVP.
 *
 * Design intent (Phase 8):
 *   - Fixed total supply minted once at deploy to the VestingVault.
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
 *   ctor:     name, symbol, totalSupply, recipient (VestingVault address)
 *   fns:      none — only constructor mints; transfers are standard OZ
 */

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Votes.sol";

contract PlatformToken is ERC20, ERC20Permit, ERC20Votes {
    constructor(
        string memory name,
        string memory symbol,
        uint256 totalSupply,
        address recipient
    ) ERC20(name, symbol) ERC20Permit(name) {
        // Mint the entire fixed supply to the VestingVault at deploy.
        // After this no more tokens can ever be created — supply is law.
        _mint(recipient, totalSupply);
    }

    // ─── ERC20Votes hooks (required by OZ) ────────────────────────────────
    function _update(address from, address to, uint256 value) internal override(ERC20, ERC20Votes) {
        super._update(from, to, value);
    }

    function _mint(address account, uint256 value) internal override(ERC20, ERC20Votes) {
        super._mint(account, value);
    }

    function _burn(address account, uint256 value) internal override(ERC20, ERC20Votes) {
        super._burn(account, value);
    }
}
