import { useState } from 'react'
import { usePrivy, type WalletWithMetadata } from '@privy-io/react-auth'
import { useExportWallet } from '@privy-io/react-auth/solana'
import { COLORS, FONTS } from '../../theme'
import { showToast } from '../../toastBus'

/**
 * Lets the player take their embedded wallet's private key with them.
 *
 * Privy renders the key itself, inside an iframe served from its own domain, so the key never
 * touches our origin and we cannot read it. All we contribute is the button.
 *
 * Only the wallet Privy created FOR THIS USER is offered. The escrow and bot wallets are owned by
 * our signing quorum rather than by a player, and Privy refuses to export those — which is the
 * behaviour we want, so the filter below is a courtesy that keeps the button honest rather than a
 * security boundary.
 *
 * Exporting does NOT revoke the session signer the player granted us: after exporting they hold a
 * key that works, and so do we. Leaving for real means revoking the delegation too, hence the
 * wording in the copy.
 */

const sectionStyle: React.CSSProperties = {
  borderRadius: 18, padding: 22,
  background: 'linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012))',
  border: `1px solid ${COLORS.border}`,
}

const shortAddr = (a: string) => `${a.slice(0, 6)}…${a.slice(-6)}`

export function ExportKeyPanel() {
  const { ready, authenticated, user } = usePrivy()
  const { exportWallet } = useExportWallet()
  const [busy, setBusy] = useState(false)

  const embedded = user?.linkedAccounts.find(
    (a): a is WalletWithMetadata =>
      a.type === 'wallet' && a.walletClientType === 'privy' && a.chainType === 'solana',
  )

  const blocked = !ready
    ? 'Loading…'
    : !authenticated
      ? 'Log in to export your key.'
      : !embedded
        ? 'This account has no embedded wallet to export.'
        : null

  async function onExport() {
    // `embedded` is non-null whenever the button is enabled, but the compiler can't see the link
    // through `blocked`, so re-check instead of asserting.
    if (busy || !embedded) return
    setBusy(true)
    try {
      // Always name the address. Without it Privy exports walletIndex 0, and an account with a
      // linked external wallet is not guaranteed to have the embedded one sitting at that index.
      await exportWallet({ address: embedded.address })
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Could not open the export dialog.', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section style={sectionStyle}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
        <div style={{ fontFamily: FONTS.mono, fontSize: 11, letterSpacing: '.2em', color: COLORS.muted }}>PRIVATE KEY</div>
        {embedded && (
          <div style={{ fontFamily: FONTS.mono, fontSize: 10, color: COLORS.muted }}>{shortAddr(embedded.address)}</div>
        )}
      </div>

      <div style={{ fontSize: 12, color: COLORS.muted, marginBottom: 14, lineHeight: 1.6 }}>
        Export your game wallet's private key to use it in Phantom or Backpack. Privy shows the key
        in its own window — it never reaches Collector Arena. Anyone holding this key controls the
        wallet and everything in it, so keep it to yourself.
        <br />
        <span style={{ color: COLORS.text }}>
          Exporting does not end battle signing access.
        </span>{' '}
        If you want that off too, revoke it from your Privy account.
      </div>

      <button
        onClick={() => void onExport()}
        disabled={!!blocked || busy}
        style={{
          padding: '12px 22px', borderRadius: 12, border: `1px solid ${COLORS.border}`,
          background: COLORS.panel2, color: blocked || busy ? COLORS.muted : COLORS.text,
          fontWeight: 800, fontSize: 14, fontFamily: FONTS.display,
          cursor: blocked ? 'not-allowed' : busy ? 'wait' : 'pointer',
          opacity: blocked || busy ? 0.6 : 1,
        }}
      >
        {busy ? 'Opening…' : 'Export private key'}
      </button>

      {blocked && (
        <div style={{ fontSize: 12, marginTop: 8, color: COLORS.muted }}>{blocked}</div>
      )}
    </section>
  )
}
