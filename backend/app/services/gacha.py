"""Proxy fino hacia la API del Gacha de Collector Crypt.

La x-api-key vive SOLO aquí (server-side). Las respuestas upstream nunca se
reenvían crudas: cada método devuelve un dict con whitelist de campos.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional

import httpx


class GachaDisabled(Exception):
    """Gacha deshabilitado: no hay gacha_base_url configurado (kill-switch)."""


#: Sent on every call to Collector Crypt: their Cloudflare rejects non-browser agents.
_USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/128.0.0.0 Safari/537.36")


class GachaUpstreamError(Exception):
    """La API del Gacha falló (4xx/5xx/timeout/JSON inválido)."""


class GachaEndpointMissing(GachaUpstreamError):
    """El endpoint no existe en ESTE host (404).

    Se distingue del resto de fallos a propósito: las redes de CC no van a la par, así que un 404
    significa "esta red todavía no tiene esta función", no "CC está caído". Es lo que permite
    canjear una tirada gratis en devnet, que sigue sin pedir nonce, sin dejar de exigirlo en
    mainnet. Como hereda de GachaUpstreamError, quien no distinga sigue tratándolo como un fallo.
    """


_MACHINE_FIELDS = ("code", "name", "price", "odds", "tierRanges", "stock", "ev", "image",
                   "shortName", "thumbnailUrl", "instantBuyback", "contains",
                   "videoSrc", "videoHevc", "turboMode", "freeSpins")
_NFT_FIELDS = ("nft_address", "name", "image", "rarity", "insured_value")
_CACHE_TTL = 60.0

# Una tirada gratis NO cuesta lo mismo en todas las máquinas: cuesta 100.000 puntos en una de 50 $
# y sube en proporción al precio, así que la de 5.000 $ vale 10 millones. Las constantes y la
# fórmula son las de la propia web de Collector Crypt.
PUNTOS_TIRADA_BASE = 100_000
PRECIO_BASE = 50


def tiradas_gratis(precio: float, puntos: int) -> dict:
    """Cuántas tiradas gratis dan `puntos` en una máquina de ese precio, y cuánto falta para la
    siguiente.

    `GET /api/freeSpins` responde `freeSpinsLeft` y `pointsPerSpin`, pero son de la wallet, NO de
    la máquina: siempre vienen calculados sobre el precio base. Usarlos tal cual decía "te quedan
    3 tiradas" en una máquina donde no llegaba ni para una. Por eso se recalcula aquí a partir del
    precio, que es lo que hace su propia web.
    """
    requeridos = round(PUNTOS_TIRADA_BASE * ((precio or PRECIO_BASE) / PRECIO_BASE))
    if requeridos <= 0:                      # precio 0 o negativo: no hay tirada que valorar
        return {"required": 0, "count": 0, "until_next": 0}
    puntos = max(0, int(puntos))
    resto = puntos % requeridos
    return {
        "required": requeridos,
        "count": puntos // requeridos,
        # Con el saldo justo no falta nada; con saldo cero falta una tirada entera, no cero.
        "until_next": 0 if (resto == 0 and puntos > 0) else requeridos - resto,
    }


class GachaService:
    def __init__(self, base_url: str, api_key: str,
                 now_fn: Callable[[], float] = time.time, timeout: float = 15.0,
                 nft_base_url: str = "https://nft-dev.collectorcrypt.com"):
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._now = now_fn
        self._timeout = timeout
        self._nft_base = nft_base_url.rstrip("/")
        self._machines_cache: Optional[tuple[float, list[dict]]] = None

    @property
    def enabled(self) -> bool:
        return bool(self._base)

    def _absolutize(self, url: Any) -> Any:
        if isinstance(url, str) and url.startswith("/"):
            return f"{self._base}{url}"
        return url

    def _check_enabled(self) -> None:
        if not self.enabled:
            raise GachaDisabled()

    async def _request(self, method: str, path: str, json: Optional[dict] = None,
                       params: Optional[dict] = None) -> Any:
        self._check_enabled()
        url = f"{self._base}{path}"
        # A BROWSER User-Agent, and it is not cosmetic. Collector Crypt sits behind Cloudflare,
        # which answers 403 to httpx's default agent. Measured on 2026-10-19: the same request with
        # `python-httpx/0.27.0` gets 403 and with a Chrome agent gets 200 — with or without the API
        # key, so it is the agent that is judged, not the credentials.
        #
        # It broke production at 11:39 that day: 1,427 failures in 24 h, the machine catalogue
        # unreachable and the gacha dark for players. We had already documented this same stumble
        # twice (privy_signer._wallet and the CC sign-in), but this client never carried it.
        headers = {"accept": "application/json", "user-agent": _USER_AGENT}
        if self._key:
            headers["x-api-key"] = self._key
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.request(method, url, json=json, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                reason = None
                try:
                    body = e.response.json()
                    if isinstance(body, dict):
                        reason = body.get("details") or body.get("error")
                except Exception:
                    reason = None
                msg = str(reason)[:140] if reason else "gacha upstream unavailable"
                if e.response.status_code == 404:
                    raise GachaEndpointMissing(msg)
                raise GachaUpstreamError(msg)
            except (httpx.HTTPError, ValueError) as e:
                raise GachaUpstreamError("gacha upstream unavailable")

    async def _availability(self) -> tuple:
        """(code -> disponible, tiradas gratis abiertas) desde /api/status.

        `freePacksStatus` es un interruptor GLOBAL de CC: cuando está en `closed` no hay tiradas
        gratis en ninguna máquina, por mucho que la máquina las ofrezca. Fail-open las dos cosas:
        si /api/status no responde, se asume todo abierto y que decida el intento real.
        """
        try:
            raw = await self._request("GET", "/api/status")
        except GachaUpstreamError:
            return {}, True
        gachas = raw.get("gachas") if isinstance(raw, dict) else None
        avail = {}
        if isinstance(gachas, list):
            for g in gachas:
                if isinstance(g, dict) and g.get("code"):
                    avail[g["code"]] = (g.get("status") == "open")
        gratis = (raw.get("freePacksStatus") if isinstance(raw, dict) else None) != "closed"
        return avail, gratis

    async def machines(self) -> list[dict]:
        self._check_enabled()
        now = self._now()
        if self._machines_cache and now - self._machines_cache[0] < _CACHE_TTL:
            return self._machines_cache[1]
        raw = await self._request("GET", "/api/machines")
        if isinstance(raw, dict):
            items = raw.get("machines", [])
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        out = [{k: m.get(k) for k in _MACHINE_FIELDS} for m in items if isinstance(m, dict)]
        for mach in out:
            for f in ("image", "thumbnailUrl", "videoSrc", "videoHevc"):
                mach[f] = self._absolutize(mach.get(f))
        avail, gratis_abiertas = await self._availability()
        for mach in out:
            mach["available"] = avail.get(mach.get("code"), True)  # default available if unknown
            # No todas las máquinas dan tiradas gratis, y encima CC puede cerrarlas de golpe. Se
            # combinan aquí en una sola respuesta a "¿puedo pedir una gratis en esta, ahora?", que
            # es lo único que necesita saber quien pinta el botón.
            las_ofrece = bool(mach.get("freeSpins"))
            mach["freeSpins"] = las_ofrece and gratis_abiertas
            # …pero combinarlas escondía un caso: la máquina SÍ las ofrece y CC las tiene cerradas.
            # Con una sola bandera eso se veía igual que "esta máquina nunca las da", o sea la
            # pantalla vacía, y un jugador con puntos de sobra no entendía por qué. Esta segunda
            # bandera es lo que permite decírselo; es un cierre temporal, no una propiedad de la
            # máquina.
            mach["freeSpinsClosed"] = las_ofrece and not gratis_abiertas
        self._machines_cache = (now, out)
        return out

    async def generate_pack(self, player_address: str, pack_type: str,
                            alt_player_address: str | None = None, turbo: bool = False) -> dict:
        body = {"playerAddress": player_address, "packType": pack_type}
        if alt_player_address:
            body["altPlayerAddress"] = alt_player_address
        if turbo:
            body["turbo"] = True
        raw = await self._request("POST", "/api/generatePack", json=body)
        return {"memo": raw.get("memo"), "transaction": raw.get("transaction")}

    async def ably_token_request(self) -> dict:
        """El `TokenRequest` de Ably para escuchar el feed de ganadores en vivo.

        Su documentación dice que hace falta `x-api-key`; medido contra las dos redes, **no**: el
        endpoint responde keyless y el token que da trae `subscribe` sobre todos los canales. Es la
        misma puerta que usa su propia web.

        Lo que devuelve NO es un token: es una petición firmada que hay que canjear en Ably. Ese
        canje vive en el ingestor, porque es cosa de Ably y no de Collector Crypt.
        """
        raw = await self._request("GET", "/api/ably/token")
        return raw if isinstance(raw, dict) else {}

    async def free_spins(self, wallet: str) -> dict:
        """Puntos de una wallet para tiradas gratis. Endpoint NO documentado.

        Devuelve SOLO lo que es de la wallet. `freeSpinsLeft`, `pointsPerSpin` y
        `pointsUntilNextSpin` vienen en la respuesta pero NO se propagan: están calculados sobre el
        precio base, así que solo valen para una máquina de 50 $. Cuántas tiradas dan estos puntos
        lo dice `tiradas_gratis(precio, puntos)`, que necesita saber en qué máquina.

        `usedPoints` son los ya gastados en tiradas gratis; lo gastable es la resta. Al no estar
        documentado el endpoint, todo se lee con `.get`.
        """
        raw = await self._request("GET", "/api/freeSpins", params={"wallet": wallet})
        puntos = raw.get("points") or 0
        gastados = raw.get("usedPoints") or 0
        return {
            "points_available": max(0, puntos - gastados),
            # Tope diario, este sí de la wallet y no de la máquina.
            "spins_left_today": raw.get("freeSpinsLeftToday") or 0,
        }

    async def generate_free_pack(self, player_address: str, pack_type: str) -> Optional[str]:
        """Nonce para canjear una tirada gratis, o None si esta red todavía no lo pide.

        Primer paso de un canje: CC devuelve un `nonce` con caducidad de minutos que hay que
        meter DENTRO de la transacción de prueba (ver `build_free_pack_proof_tx`) y repetir en el
        cuerpo de `free_pack`.

        **Devuelve None cuando el endpoint no existe (404)**, que es el caso de devnet: allí el
        canje sigue siendo el de antes. Las redes de CC no van a la par y exigir el nonce en las
        dos dejó devnet sin tiradas gratis, con un 502 mudo. Cualquier OTRO fallo sí se propaga:
        confundir una caída de CC con "esta red es la vieja" mandaría el formato antiguo a mainnet
        y el jugador acabaría viendo "Missing or invalid nonce", que no explica nada.
        """
        try:
            raw = await self._request("POST", "/api/generateFreePack", json={
                "publicKey": player_address, "packType": pack_type,
            })
        except GachaEndpointMissing:
            return None
        nonce = raw.get("nonce")
        if not nonce:
            raise GachaUpstreamError("gacha upstream unavailable")
        return nonce

    async def free_pack(self, player_address: str, pack_type: str, signed_transaction: str,
                        nonce: Optional[str] = None, turbo: bool = False) -> dict:
        """Canjea una tirada gratis. Endpoint NO documentado.

        `signedTransaction` es una transacción entera firmada por esa wallet, en base64; sirve de
        prueba de propiedad y NO se envía a la cadena. Devuelve el `memo` del sobre, que se abre
        después con el mismo `open_pack` que uno de pago.

        OJO con el `nonce`, que es lo que rompió este canje una vez: va por DOS vías a la vez, en
        el cuerpo y dentro de la transacción firmada, y CC comprueba las dos. Aquí ya no vale
        cualquier transacción firmada, que es lo que aceptaba antes.

        La carta va SIEMPRE a `player_address`: `altPlayerAddress` se acepta en el cuerpo pero se
        ignora (comprobado on-chain). No sirve para entregarla a un tercero.
        """
        cuerpo = {"publicKey": player_address, "packType": pack_type,
                  "turbo": turbo, "transactionSignature": signed_transaction}
        # Sin nonce ni siquiera va la clave: en las redes que no lo piden, mandarla vacía sería
        # inventarse un contrato que allí no existe.
        if nonce is not None:
            cuerpo["nonce"] = nonce
        raw = await self._request("POST", "/api/freePack", json=cuerpo)
        return {"memo": raw.get("memo"), "remaining_points": raw.get("remainingPoints")}

    async def generate_yolo_packs(self, player_address: str, pack_type: str,
                                  count: int, turbo: bool) -> dict:
        raw = await self._request("POST", "/api/generateYoloPacks", json={
            "playerAddress": player_address, "packType": pack_type,
            "count": count, "turbo": turbo,
        })
        txs = raw.get("transactions") if isinstance(raw, dict) else None
        out = []
        for t in txs or []:
            if isinstance(t, dict) and t.get("memo") and t.get("transaction"):
                out.append({"memo": t["memo"], "transaction": t["transaction"]})
        return {"yolo_id": raw.get("yoloId") if isinstance(raw, dict) else None,
                "count": raw.get("count") if isinstance(raw, dict) else None,
                "transactions": out}

    async def submit_tx(self, signed_transaction: str) -> dict:
        raw = await self._request("POST", "/api/submitTransaction",
                                  json={"signedTransaction": signed_transaction})
        return {"signature": raw.get("signature"),
                "confirmation_status": raw.get("confirmationStatus")}

    async def buyback_available(self, wallet: str, nft: str) -> dict:
        raw = await self._request("GET", "/api/buyback/available",
                                  params={"wallet": wallet, "nft": nft})
        available = bool(raw.get("available")) if isinstance(raw, dict) else False
        amount = raw.get("amount") if (isinstance(raw, dict) and available) else None
        return {"available": available, "amount": amount}

    async def buyback(self, player_address: str, nft_address: str) -> dict:
        raw = await self._request("POST", "/api/buyback",
                                  json={"playerAddress": player_address, "nftAddress": nft_address})
        return {
            "serialized_transaction": raw.get("serializedTransaction"),
            "refund_amount": raw.get("refundAmount"),
            "memo": raw.get("memo"),
        }

    async def open_pack(self, memo: str) -> dict:
        raw = await self._request("POST", "/api/openPack", json={"memo": memo})
        if raw.get("code") == "WAITING_FOR_WEBHOOK":
            return {"pending": True}
        if not raw.get("nft_address"):
            raise GachaUpstreamError("gacha upstream: openPack response with no nft_address")
        nft_won = raw.get("nftWon") or {}
        content = nft_won.get("content") or {}
        metadata = content.get("metadata") or {}
        attributes = nft_won.get("attributes") or metadata.get("attributes") or []
        attr = {t.get("trait_type"): t.get("value") for t in attributes if isinstance(t, dict)}
        name = metadata.get("name") or nft_won.get("name")

        # images: prefer content.files (cc_cdn > cdn_uri > uri); fallback to the single image
        images = self._extract_images(content, nft_won.get("image"))

        # insured value: top-level number, else the "Insured Value" attribute
        insured = nft_won.get("insured_value")
        if insured is None:
            iv = attr.get("Insured Value")
            if isinstance(iv, (int, float)):
                insured = iv
            elif isinstance(iv, str):
                try:
                    insured = float(iv.replace(",", "").strip())
                except ValueError:
                    pass

        authed = attr.get("Authenticated")
        authenticated = (str(authed).strip().lower() == "true") if authed is not None else None

        auto_sold = raw.get("code") == "TURBO_MODE_BUYBACK"
        buyback_amount = raw.get("buybackAmount") if auto_sold else None

        return {
            "pending": False,
            "nft_address": raw.get("nft_address"),
            "rarity": raw.get("rarity"),
            "name": name,
            "image": images[0] if images else nft_won.get("image"),
            "images": images,
            "year": self._extract_year(attributes, name),
            "grade": self._extract_grade(attributes),
            "grading_company": attr.get("Grading Company"),
            "grading_id": attr.get("Grading ID"),
            "authenticated": authenticated,
            "insured_value": insured,
            "auto_sold": auto_sold,
            "buyback_amount": buyback_amount,
        }

    @staticmethod
    def _extract_images(content: dict, fallback: Optional[str]) -> list:
        images: list = []
        for f in (content.get("files") or []):
            if isinstance(f, dict):
                u = f.get("cc_cdn") or f.get("cdn_uri") or f.get("uri")
                if u and u not in images:
                    images.append(u)
        if not images and fallback:
            images = [fallback]
        return images

    @staticmethod
    def _extract_grade(attributes: list) -> Optional[str]:
        a = {t.get("trait_type"): t.get("value") for t in attributes if isinstance(t, dict)}
        company = (a.get("Grading Company") or "").strip()
        label = str(a.get("The Grade") or a.get("GradeNum") or "").strip()
        grade = f"{company} {label}".strip()
        return grade or None

    @staticmethod
    def _extract_year(attributes: list, name: Optional[str] = None) -> Optional[str]:
        a = {t.get("trait_type"): t.get("value") for t in attributes if isinstance(t, dict)}
        year = a.get("Year")
        if year:
            return str(year)
        if name:
            import re
            m = re.match(r"\s*(\d{4})\b", name)
            if m:
                return m.group(1)
        return None

    # prize_tier de CC → nombre de rareza. Verificado contra 200 filas reales cruzando el
    # insuredValue con el tierRange de su máquina: 1=Epic, 2=Rare, 3=Uncommon, 4=Common.
    _TIERS = {1: "Epic", 2: "Rare", 3: "Uncommon", 4: "Common"}

    async def winners(self, pack_type: Optional[str] = None, count: int = 10,
                      epic_only: bool = False) -> list:
        """Últimos ganadores del gacha, de toda la plataforma.

        `count` se recorta a 200 porque es el máximo que sirve CC: pedirle más devuelve 200 igual, y
        prometer en la UI un número que la API no puede dar sería mentir. El endpoint paginado sin
        tope exige un `slug`, o sea una API key que no tenemos.

        `epic_only` usa el filtro propio de CC en vez de traer una página y quedarse con los Epic:
        solo 1 de cada 100 tiradas lo es, así que filtrar después devolvería dos o tres resultados.
        Para las demás rarezas no hay filtro upstream y el recorte se hace aquí.
        """
        self._check_enabled()
        params: dict = {"count": max(1, min(int(count), 200))}
        if pack_type:
            params["packType"] = pack_type
        if epic_only:
            params["epic"] = "true"
        raw = await self._request("GET", "/api/getAllWinners", params=params)
        items = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        out = []
        for w in items:
            if not isinstance(w, dict):
                continue
            nft = w.get("nft") or {}
            content = nft.get("content") or {}
            meta = content.get("metadata") or {}
            out.append({
                "wallet": w.get("winner"),
                "nft_address": w.get("nft_address"),
                "name": meta.get("json_name") or meta.get("name"),
                "images": self._extract_images(content, nft.get("image")),
                "insured_value": w.get("insuredValue"),
                "machine": w.get("pack_type"),
                "rarity": self._TIERS.get(w.get("prize_tier")),
                "at": w.get("created_at"),
                "slug": w.get("memo_slug"),
            })
        return out

    #: Lo único que el EV tracker necesita de una tirada. Se mantiene la whitelist del módulo: no
    #: se reenvía la respuesta cruda, solo se conservan los NOMBRES de CC para que el normalizador
    #: pueda tratar esta fuente y la del feed en vivo con el mismo código.
    _WINNER_RAW = ("nft_address", "pack_type", "prize_tier", "insuredValue", "created_at", "winner")

    async def winners_raw(self, pack_type: str, count: int = 200,
                          timestamp: Optional[str] = None) -> list[dict]:
        """Ganadores con los nombres de campo de Collector Crypt, para el ingestor del EV tracker.

        `timestamp` acota a lo posterior a ese instante (ISO 8601). Reduce el tamaño de la
        respuesta, pero NO levanta el tope de 200: si en ese rato hubo más, se pierden las de en
        medio, y eso lo detecta el ingestor.
        """
        self._check_enabled()
        params: dict = {"count": max(1, min(int(count), 200)), "packType": pack_type}
        if timestamp:
            params["timestamp"] = timestamp
        raw = await self._request("GET", "/api/getAllWinners", params=params)
        items = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        return [{k: w.get(k) for k in self._WINNER_RAW} for w in items if isinstance(w, dict)]

    async def get_nfts(self, code: str, rarity: Optional[str] = None,
                       page: int = 1, limit: int = 20) -> list:
        self._check_enabled()
        params: dict = {"code": code, "page": page, "limit": limit}
        if rarity:
            params["rarity"] = rarity
        raw = await self._request("GET", "/api/getNfts", params=params)
        if isinstance(raw, dict):
            items = raw.get("nfts", [])
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        out = []
        for n in items:
            if not isinstance(n, dict):
                continue
            attributes = n.get("attributes") or []
            a = {t.get("trait_type"): t.get("value") for t in attributes if isinstance(t, dict)}
            authed = a.get("Authenticated")
            gradenum = a.get("GradeNum")
            card = {k: n.get(k) for k in _NFT_FIELDS}
            card["grade"] = self._extract_grade(attributes)
            card["images"] = self._extract_images(n.get("content") or {}, n.get("image"))
            card["grading_company"] = a.get("Grading Company")
            card["grading_id"] = a.get("Grading ID")
            card["the_grade"] = a.get("The Grade")
            card["generic_grade"] = str(gradenum) if gradenum is not None else None
            card["authenticated"] = (str(authed).strip().lower() == "true") if authed is not None else None
            card["year"] = self._extract_year(attributes, n.get("name"))
            out.append(card)
        return out

    @staticmethod
    def _parse_insured_value(iv: Any) -> Optional[float]:
        """CC's 'Insured Value' attribute can be a number or a money string like '$5,000.00'."""
        if iv is None:
            return None
        if isinstance(iv, (int, float)):
            return float(iv)
        s = re.sub(r"[^0-9.\-]", "", str(iv))
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    async def nft_metadata(self, mint: str) -> dict:
        """Fetch a single card's metadata BY MINT from CC's public, keyless metadata host
        (devnet: nft-dev.collectorcrypt.com; mainnet: nft.collectorcrypt.com). DAS returns null
        metadata on devnet, so this is the reliable source for the inventory card modal. The
        upstream JSON is parsed/whitelisted here — never forwarded raw (same as get_nfts/open_pack)."""
        self._check_enabled()
        url = f"{self._nft_base}/metadata/{mint}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.get(url, headers={"accept": "application/json"})
                resp.raise_for_status()
                raw = resp.json()
            except (httpx.HTTPError, ValueError):
                raise GachaUpstreamError("gacha upstream unavailable")
        if not isinstance(raw, dict):
            raise GachaUpstreamError("gacha upstream: invalid metadata")
        attributes = raw.get("attributes") or []
        attr = {t.get("trait_type"): t.get("value") for t in attributes if isinstance(t, dict)}
        name = raw.get("name")
        authed = attr.get("Authenticated")
        authenticated = (str(authed).strip().lower() == "true") if authed is not None else None
        rarity = attr.get("Rarity")
        return {
            "nft_address": mint,
            "name": name,
            "image": raw.get("image"),
            "rarity": str(rarity).lower() if rarity is not None else None,
            "insured_value": self._parse_insured_value(attr.get("Insured Value")),
            "grade": self._extract_grade(attributes),
            "grading_company": attr.get("Grading Company"),
            "grading_id": attr.get("Grading ID"),
            "year": self._extract_year(attributes, name),
            "authenticated": authenticated,
        }
