const { expect } = require('chai');
const { ethers, network } = require('hardhat');

const UNIT = 10n ** 18n;
const COMMUNITY = 509_414_048n * UNIT;
const TEAM = 130_585_952n * UNIT;
const CHAMPIONS = 160_000_000n * UNIT;
const VAULT = 800_000_000n * UNIT;

function leafFor(account, amount) {
  return ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(['address', 'uint256'], [account, amount]),
  );
}

async function advance(seconds) {
  await network.provider.send('evm_increaseTime', [seconds]);
  await network.provider.send('evm_mine');
}

async function expectRevert(promise, message) {
  try {
    await promise;
    expect.fail(`Expected revert containing: ${message}`);
  } catch (error) {
    expect(String(error.message)).to.include(message);
  }
}

async function deployFixture() {
  const [governance, keeper, team, claimant, other] = await ethers.getSigners();
  const Token = await ethers.getContractFactory('PlatformToken');
  const token = await Token.deploy('DCF Test Token', 'DCF', 1_000_000_000n * UNIT, governance.address);
  const Registry = await ethers.getContractFactory('ModelRegistry');
  const registry = await Registry.deploy(governance.address);
  const Distributor = await ethers.getContractFactory('EpochDistributor');
  const distributor = await Distributor.deploy(await token.getAddress(), governance.address, await registry.getAddress());
  const Vault = await ethers.getContractFactory('VestingVault');
  const vault = await Vault.deploy(await token.getAddress(), await distributor.getAddress(), team.address);
  await distributor.setVestingVault(await vault.getAddress());
  await distributor.setKeeper(keeper.address);
  await token.transfer(await vault.getAddress(), VAULT);
  return { governance, keeper, team, claimant, other, token, registry, distributor, vault };
}

describe('DCF VestingVault and EpochDistributor', function () {
  it('locks the exact 1B fixture and decaying 40-epoch allocation', async function () {
    const { token, vault, distributor, team } = await deployFixture();
    let previousCommunity = 0n;
    for (let epoch = 1; epoch <= 40; epoch += 1) {
      await advance(90 * 24 * 60 * 60);
      await vault.releaseEpoch();
      const event = await distributor.epochs(epoch);
      expect(event.funded > 0n).to.equal(true);
      if (epoch < 40) expect(event.funded <= (previousCommunity || event.funded)).to.equal(true);
      previousCommunity = event.funded;
    }
    expect(await vault.communityReleased()).to.equal(COMMUNITY);
    expect(await vault.teamReleased()).to.equal(TEAM);
    expect(await token.balanceOf(team.address)).to.equal(TEAM);
    expect(await token.balanceOf(await vault.getAddress())).to.equal(CHAMPIONS);
    expect(await distributor.totalReserved()).to.equal(COMMUNITY);
  });

  it('requires an approved model, exact funding, seven-day challenge, and one claim', async function () {
    const { keeper, claimant, token, registry, distributor, vault } = await deployFixture();
    await advance(90 * 24 * 60 * 60);
    await vault.releaseEpoch();
    const funded = (await distributor.epochs(1)).funded;
    const modelHash = ethers.keccak256(ethers.toUtf8Bytes('governed-v1-build'));
    const proofHash = ethers.keccak256(ethers.toUtf8Bytes('ipfs-proof-payload'));
    const root = leafFor(claimant.address, funded);
    await expectRevert(
      distributor.connect(keeper).proposeRoot(1, root, funded, modelHash, proofHash),
      'EpochDistributor: model not approved',
    );
    await registry.approveModel(modelHash, 1);
    const nextModelHash = ethers.keccak256(ethers.toUtf8Bytes('governed-v2-build'));
    await registry.approveModel(nextModelHash, 2);
    expect(await registry.isActive(nextModelHash, 1)).to.equal(false);
    expect(await registry.isActive(nextModelHash, 2)).to.equal(true);
    await expectRevert(
      distributor.connect(keeper).proposeRoot(1, root, funded - 1n, modelHash, proofHash),
      'EpochDistributor: allocation must equal funding',
    );
    await distributor.connect(keeper).proposeRoot(1, root, funded, modelHash, proofHash);
    await expectRevert(
      distributor.connect(claimant).finalizeRoot(1),
      'EpochDistributor: challenge open',
    );
    await advance(7 * 24 * 60 * 60);
    await distributor.finalizeRoot(1);
    await distributor.connect(claimant).claim(1, funded, []);
    expect(await token.balanceOf(claimant.address)).to.equal(funded);
    await expectRevert(
      distributor.connect(claimant).claim(1, funded, []),
      'EpochDistributor: already claimed',
    );
  });

  it('allows governance to veto during challenge and returns expired claims to the terminal reserve', async function () {
    const { governance, keeper, claimant, token, registry, distributor, vault } = await deployFixture();
    await advance(90 * 24 * 60 * 60);
    await vault.releaseEpoch();
    const funded = (await distributor.epochs(1)).funded;
    const modelHash = ethers.keccak256(ethers.toUtf8Bytes('governed-v1-build'));
    const proofHash = ethers.keccak256(ethers.toUtf8Bytes('ipfs-proof-payload'));
    const root = leafFor(claimant.address, funded);
    await registry.approveModel(modelHash, 1);
    await distributor.connect(keeper).proposeRoot(1, root, funded, modelHash, proofHash);
    await distributor.connect(governance).vetoRoot(1);
    expect((await distributor.epochs(1)).status).to.equal(1n);
    await distributor.connect(keeper).proposeRoot(1, root, funded, modelHash, proofHash);
    await advance(7 * 24 * 60 * 60);
    await distributor.finalizeRoot(1);
    await advance(365 * 24 * 60 * 60 + 1);
    const vaultBefore = await token.balanceOf(await vault.getAddress());
    await distributor.returnExpiredFunds(1);
    expect(await token.balanceOf(await vault.getAddress())).to.equal(vaultBefore + funded);
    expect((await distributor.epochs(1)).status).to.equal(4n);
  });

  it('does not release Champions before day 3650 and funds terminal epoch only afterwards', async function () {
    const { distributor, vault } = await deployFixture();
    for (let epoch = 1; epoch <= 40; epoch += 1) {
      await advance(90 * 24 * 60 * 60);
      await vault.releaseEpoch();
    }
    await expectRevert(vault.releaseChampions(), 'VestingVault: terminal not due');
    await advance(50 * 24 * 60 * 60);
    await vault.releaseChampions();
    expect((await distributor.epochs(41)).funded).to.equal(CHAMPIONS);
  });
});
