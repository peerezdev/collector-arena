"""Tests for build_nft_transfer — SPL NFT escrow→winner transaction builder."""
import base64

import pytest
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.token.associated import get_associated_token_address

from solders.keypair import Keypair

from app.services.solana_tx import (
    build_nft_transfer,
    build_token_multi_transfer,
    build_token_transfer,
    leer_firma,
    TOKEN_PROGRAM,
    ATA_PROGRAM,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
ESCROW   = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
DEST     = "A4ahkivAG4NoZAE8Sy4qv8nn2DU9yoXRQcttuCeGtTJv"
MINT     = "So11111111111111111111111111111111111111112"   # wrapped SOL mint (valid devnet-style pubkey)
BLOCKHASH = "11111111111111111111111111111111"             # 32-zero-byte hash, always valid

TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Throwaway keys to be able to really SIGN in the `leer_firma` tests: without the private key
# there is no way to fill in a signature slot, and with a fake signature it would not be possible
# to tell "signed" apart from "unsigned", which is exactly what needs to be tested.
OPERADOR = Keypair()
OPERADOR_PUB = str(OPERADOR.pubkey())
JUGADOR = Keypair()
JUGADOR_PUB = str(JUGADOR.pubkey())


@pytest.fixture()
def default_tx() -> Transaction:
    """Build a transaction with default (classic SPL) token program and decode it."""
    out = build_nft_transfer(ESCROW, DEST, MINT, BLOCKHASH)
    return Transaction.from_bytes(base64.b64decode(out))


# ---------------------------------------------------------------------------
# Test 1: basic structure
# ---------------------------------------------------------------------------
class TestBuildNftTransferStructure:
    def test_returns_string(self):
        out = build_nft_transfer(ESCROW, DEST, MINT, BLOCKHASH)
        assert isinstance(out, str)

    def test_base64_roundtrip(self):
        out = build_nft_transfer(ESCROW, DEST, MINT, BLOCKHASH)
        tx = Transaction.from_bytes(base64.b64decode(out))
        assert tx is not None

    def test_fee_payer_is_escrow(self, default_tx):
        assert default_tx.message.account_keys[0] == Pubkey.from_string(ESCROW)

    def test_exactly_two_instructions(self, default_tx):
        assert len(default_tx.message.instructions) == 2

    def test_ata_create_instruction_data(self, default_tx):
        """The ATA-create instruction must use discriminator 1 (CreateIdempotent)."""
        ata_prog = Pubkey.from_string(ATA_PROGRAM)
        keys = default_tx.message.account_keys
        create_ix = next(
            ix for ix in default_tx.message.instructions
            if keys[ix.program_id_index] == ata_prog
        )
        # Compiled instruction data is bytes-like
        assert bytes(create_ix.data) == bytes([1])

    def test_transfer_checked_instruction_data(self, default_tx):
        """transfer_checked must use discriminator 12, amount=1 LE u64, decimals=0."""
        token_prog = Pubkey.from_string(TOKEN_PROGRAM)
        keys = default_tx.message.account_keys
        transfer_ix = next(
            ix for ix in default_tx.message.instructions
            if keys[ix.program_id_index] == token_prog
        )
        expected = bytes([12]) + (1).to_bytes(8, "little") + bytes([0])
        assert bytes(transfer_ix.data) == expected

    def test_src_ata_in_account_keys(self, default_tx):
        token_prog = Pubkey.from_string(TOKEN_PROGRAM)
        src_ata = get_associated_token_address(
            Pubkey.from_string(ESCROW), Pubkey.from_string(MINT), token_prog
        )
        assert src_ata in default_tx.message.account_keys

    def test_dest_ata_in_account_keys(self, default_tx):
        token_prog = Pubkey.from_string(TOKEN_PROGRAM)
        dest_ata = get_associated_token_address(
            Pubkey.from_string(DEST), Pubkey.from_string(MINT), token_prog
        )
        assert dest_ata in default_tx.message.account_keys

    def test_transfer_checked_account_order(self, default_tx):
        """transfer_checked accounts MUST be [src_ata, mint, dest_ata, authority] in order —
        a swapped src/dest would send the NFT to the wrong account yet pass presence checks."""
        token_prog = Pubkey.from_string(TOKEN_PROGRAM)
        keys = default_tx.message.account_keys
        transfer_ix = next(
            ix for ix in default_tx.message.instructions
            if keys[ix.program_id_index] == token_prog
        )
        src_ata = get_associated_token_address(
            Pubkey.from_string(ESCROW), Pubkey.from_string(MINT), token_prog
        )
        dest_ata = get_associated_token_address(
            Pubkey.from_string(DEST), Pubkey.from_string(MINT), token_prog
        )
        accts = transfer_ix.accounts
        assert keys[accts[0]] == src_ata               # source = escrow's ATA
        assert keys[accts[1]] == Pubkey.from_string(MINT)
        assert keys[accts[2]] == dest_ata              # destination = winner's ATA
        assert keys[accts[3]] == Pubkey.from_string(ESCROW)  # authority


# ---------------------------------------------------------------------------
# Test 2: custom token_program (Token-2022)
# ---------------------------------------------------------------------------
class TestBuildNftTransferToken2022:
    def test_token2022_transfer_ix_uses_custom_program(self):
        """Passing Token-2022 id routes the transfer_checked to that program."""
        out = build_nft_transfer(ESCROW, DEST, MINT, BLOCKHASH, token_program=TOKEN_2022)
        tx = Transaction.from_bytes(base64.b64decode(out))
        token2022_pk = Pubkey.from_string(TOKEN_2022)
        keys = tx.message.account_keys
        transfer_ix = next(
            ix for ix in tx.message.instructions
            if keys[ix.program_id_index] == token2022_pk
        )
        expected = bytes([12]) + (1).to_bytes(8, "little") + bytes([0])
        assert bytes(transfer_ix.data) == expected

    def test_token2022_ata_create_uses_custom_token_prog(self):
        """ATA-create instruction must reference Token-2022 as the token program."""
        out = build_nft_transfer(ESCROW, DEST, MINT, BLOCKHASH, token_program=TOKEN_2022)
        tx = Transaction.from_bytes(base64.b64decode(out))
        token2022_pk = Pubkey.from_string(TOKEN_2022)
        ata_prog_pk  = Pubkey.from_string(ATA_PROGRAM)
        keys = tx.message.account_keys
        create_ix = next(
            ix for ix in tx.message.instructions
            if keys[ix.program_id_index] == ata_prog_pk
        )
        # The token program (last account in create ix) must be Token-2022
        last_account_idx = create_ix.accounts[-1]
        assert keys[last_account_idx] == token2022_pk

    def test_token2022_fee_payer_still_escrow(self):
        out = build_nft_transfer(ESCROW, DEST, MINT, BLOCKHASH, token_program=TOKEN_2022)
        tx = Transaction.from_bytes(base64.b64decode(out))
        assert tx.message.account_keys[0] == Pubkey.from_string(ESCROW)


# ---------------------------------------------------------------------------
# Test 3: generalised build_token_transfer
# ---------------------------------------------------------------------------
def test_build_token_transfer_usdc_amount_decimals_and_feepayer():
    from app.services.solana_tx import build_token_transfer, TOKEN_PROGRAM
    import base64
    from solders.transaction import Transaction
    from solders.pubkey import Pubkey
    ESCROW="9oZgd4eviozqaYu7KwCTctAYgsRTWtF3McJARaztPsRQ"
    PLAYER="8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
    OP="A4ahkivAG4NoZAE8Sy4qv8nn2DU9yoXRQcttuCeGtTJv"
    USDC="Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"
    out = build_token_transfer(PLAYER, ESCROW, USDC, "11111111111111111111111111111111",
                               amount=50_000_000, decimals=6, fee_payer=OP)
    tx = Transaction.from_bytes(base64.b64decode(out))
    keys = tx.message.account_keys
    assert keys[0] == Pubkey.from_string(OP)   # fee payer = operator
    tok = Pubkey.from_string(TOKEN_PROGRAM)
    ix = next(i for i in tx.message.instructions if keys[i.program_id_index] == tok)
    assert bytes(ix.data) == bytes([12]) + (50_000_000).to_bytes(8,"little") + bytes([6])


def test_build_create_ata_single_idempotent_ix_operator_payer():
    from app.services.solana_tx import build_create_ata, ATA_PROGRAM, TOKEN_PROGRAM
    import base64
    from solders.transaction import Transaction
    from solders.pubkey import Pubkey
    from solders.token.associated import get_associated_token_address
    OWNER = "9oZgd4eviozqaYu7KwCTctAYgsRTWtF3McJARaztPsRQ"
    OP    = "A4ahkivAG4NoZAE8Sy4qv8nn2DU9yoXRQcttuCeGtTJv"
    USDC  = "Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"
    out = build_create_ata(OWNER, USDC, "11111111111111111111111111111111", payer=OP)
    tx = Transaction.from_bytes(base64.b64decode(out))
    keys = tx.message.account_keys
    assert keys[0] == Pubkey.from_string(OP)               # fee payer = operator
    assert len(tx.message.instructions) == 1
    ix = tx.message.instructions[0]
    assert keys[ix.program_id_index] == Pubkey.from_string(ATA_PROGRAM)
    assert bytes(ix.data) == bytes([1])                    # CreateIdempotent
    ata = get_associated_token_address(Pubkey.from_string(OWNER),
                                       Pubkey.from_string(USDC),
                                       Pubkey.from_string(TOKEN_PROGRAM))
    a = [str(keys[i]) for i in ix.accounts]
    assert a[1] == str(ata) and a[2] == OWNER              # ATA + its owner


# ---------------------------------------------------------------------------
# Operator-sponsored fee-payer (NFT withdraw) + fee-split multi-transfer (USDC withdraw fee)
# ---------------------------------------------------------------------------
OPERATOR   = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"   # any valid distinct pubkey (Token-2022 id)
FEE_WALLET = "5DfUc9vcvLBNCTrzWXsXrEdD8x8DoPuYxLYoAytXuub9"


class TestOperatorFeePayer:
    def test_nft_transfer_operator_is_fee_payer(self):
        """fee_payer=OPERATOR → operator is the tx fee-payer; the owner is still present (authority)."""
        tx = Transaction.from_bytes(base64.b64decode(
            build_nft_transfer(ESCROW, DEST, MINT, BLOCKHASH, fee_payer=OPERATOR)))
        assert tx.message.account_keys[0] == Pubkey.from_string(OPERATOR)
        assert Pubkey.from_string(ESCROW) in tx.message.account_keys

    def test_nft_transfer_default_fee_payer_is_owner(self):
        """No fee_payer → owner pays (unchanged escrow→winner behaviour)."""
        tx = Transaction.from_bytes(base64.b64decode(
            build_nft_transfer(ESCROW, DEST, MINT, BLOCKHASH)))
        assert tx.message.account_keys[0] == Pubkey.from_string(ESCROW)


class TestMultiTransferFeeSplit:
    def test_two_transfers_with_operator_fee_payer(self):
        """net→dest + fee→fee_wallet in one tx: operator is fee-payer, 4 instructions, both ATAs present."""
        tx = Transaction.from_bytes(base64.b64decode(build_token_multi_transfer(
            ESCROW, [(DEST, 99_000_000), (FEE_WALLET, 1_000_000)], MINT, BLOCKHASH,
            decimals=6, fee_payer=OPERATOR)))
        keys = tx.message.account_keys
        assert keys[0] == Pubkey.from_string(OPERATOR)             # operator sponsors
        assert len(tx.message.instructions) == 4                   # create+transfer per destination
        for dest in (DEST, FEE_WALLET):
            dest_ata = get_associated_token_address(Pubkey.from_string(dest), Pubkey.from_string(MINT),
                                                    Pubkey.from_string(TOKEN_PROGRAM))
            assert dest_ata in keys


# ---------------------------------------------------------------------------
# leer_firma: the signature lives INSIDE the transaction, the RPC does not invent it
# ---------------------------------------------------------------------------
class TestLeerFirma:
    """Being able to read the signature before sending is what allows recording in the database
    "I'm about to send THIS transaction" BEFORE sending it. The only thing that needs guarding is
    that it does not mistake an UNSIGNED transaction (whose slot is 64 zeros, which in base58
    look like a perfectly presentable `1111…`) for a signed one."""

    def _tx_sin_firmar(self):
        # Mirrors the charge's: the player is the USDC authority and the operator pays the fee,
        # so the transaction carries TWO signers and the one in slot 0 is the operator.
        return build_token_transfer(JUGADOR_PUB, DEST, MINT, BLOCKHASH,
                                    amount=10, decimals=6, fee_payer=OPERADOR_PUB)

    def test_an_UNSIGNED_tx_does_not_pass_as_signed(self):
        # The whole trap this helper guards against: `str(Signature.default())` is "1111…", a
        # string that looks like a signature and that, saved to the database, would be
        # impossible to reconcile with anything.
        with pytest.raises(ValueError, match="is not signed"):
            leer_firma(self._tx_sin_firmar())

    def test_a_signed_tx_returns_ITS_signature(self):
        tx = Transaction.from_bytes(base64.b64decode(self._tx_sin_firmar()))
        tx.partial_sign([OPERADOR], tx.message.recent_blockhash)
        firmada = base64.b64encode(bytes(tx)).decode()

        firma = leer_firma(firmada)
        assert firma == str(tx.signatures[0])
        assert set(firma) != {"1"}, "not the all-zeros signature"
        # And it is the FEE PAYER's, which is the one the RPC would return as the result of
        # sendTransaction: `signatures[i]` corresponds to `account_keys[i]`, and `account_keys[0]`
        # is whoever pays.
        assert tx.message.account_keys[0] == OPERADOR.pubkey()

    def test_signing_ONLY_with_the_other_signer_is_not_enough(self):
        # The charge's transaction carries two signers: the player (USDC authority) and the
        # operator (fee payer). If only the player signed, slot 0 would still be at zeros and
        # the transaction would not be sendable: returning "a signature" there would be a lie.
        tx = Transaction.from_bytes(base64.b64decode(self._tx_sin_firmar()))
        tx.partial_sign([JUGADOR], tx.message.recent_blockhash)
        with pytest.raises(ValueError, match="is not signed"):
            leer_firma(base64.b64encode(bytes(tx)).decode())

    def test_something_that_is_not_a_transaction_fails_clearly(self):
        # If Privy returned garbage, an explicit ValueError is better than an IndexError from who
        # knows where: the caller treats it as "the charge could not be prepared" and leaves no
        # trace.
        with pytest.raises(ValueError, match="could not parse"):
            leer_firma("this-is-not-base64-of-a-tx")
