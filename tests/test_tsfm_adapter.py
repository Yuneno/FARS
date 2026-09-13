"""Tests for the TSFM-to-FARS trade conversion adapter (P1-B1)."""

import math
from datetime import datetime, timezone

import pytest

from src.tsfm_adapter import (
    ConversionConfig,
    ConversionError,
    ConversionResult,
    ConversionWarning,
    convert_trades,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_record(**overrides) -> dict:
    rec = {
        "t": "2024-06-01T10:00:00+00:00",
        "strategy": "test",
        "side": 1,
        "entry": 100.0,
        "stop": 95.0,
        "exit_price": 110.0,
        "pnl_net": 10.0,
        "outcome": "closed",
        "asset": "MNQ",
    }
    rec.update(overrides)
    return rec


# ===========================================================================
# 1. Long y short con R conocido
# ===========================================================================

class TestLongShortR:
    def test_long_r_known(self):
        rec = _base_record(side=1, entry=100, stop=95, pnl_net=10)
        result = convert_trades([rec])
        assert result.accepted == 1
        trade = result.trades[0]
        assert trade.direction == "long"
        assert trade.r_result == pytest.approx(2.0)  # 10 / 5

    def test_short_r_known(self):
        rec = _base_record(side=-1, entry=110, stop=115, pnl_net=6)
        result = convert_trades([rec])
        assert result.accepted == 1
        trade = result.trades[0]
        assert trade.direction == "short"
        assert trade.r_result == pytest.approx(1.2)  # TSFM: pnl_net / abs(risk), no short negation

    def test_short_loss(self):
        rec = _base_record(side=-1, entry=110, stop=115, pnl_net=-6)
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.trades[0].r_result == pytest.approx(-1.2)  # negative pnl_net -> negative R


# ===========================================================================
# 2. PnL en puntos y en dinero; costes netos sin doble descuento
# ===========================================================================

class TestPnlUnits:
    # --- Req 1: pnl_units exacto ---
    def test_points_default(self):
        rec = _base_record(pnl_net=5, entry=100, stop=98)
        result = convert_trades([rec])
        assert result.pnl_units_used == "points"
        assert result.trades[0].r_result == pytest.approx(2.5)  # 5 / 2

    def test_pnl_units_invalid_rejected(self):
        """pnl_units!='points'/'monetary' â†’ rechazado antes de procesar."""
        cfg = ConversionConfig(pnl_units="dollars")
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_PNL_UNITS"

    # --- Req 2-4: monetary param validation ---
    def test_monetary_known_r(self):
        """R = pnl_net / (risk * qty * dpp) = 200 / (5 * 2 * 2) = 10."""
        rec = _base_record(pnl_net=200, entry=100, stop=95)
        cfg = ConversionConfig(pnl_units="monetary", quantity=2, dollar_per_point=2.0)
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        assert result.trades[0].r_result == pytest.approx(10.0)

    def test_monetary_metadata_recorded(self):
        """metadata incluye monetary params efectivos."""
        rec = _base_record(pnl_net=100, entry=100, stop=90)
        cfg = ConversionConfig(pnl_units="monetary", quantity=3, dollar_per_point=5.0)
        result = convert_trades([rec], config=cfg)
        m = result.trades[0].metadata
        assert m["pnl_units"] == "monetary"
        assert m["quantity"] == 3
        assert m["dollar_per_point"] == 5.0

    def test_monetary_quantity_zero_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=0, dollar_per_point=2.0)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_QUANTITY"

    def test_monetary_quantity_negative_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=-1, dollar_per_point=2.0)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_QUANTITY"

    def test_monetary_quantity_float_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=2.5, dollar_per_point=2.0)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_QUANTITY"

    def test_monetary_quantity_bool_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=True, dollar_per_point=2.0)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_QUANTITY"

    def test_monetary_dpp_zero_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=2, dollar_per_point=0)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_DOLLAR_PER_POINT"

    def test_monetary_dpp_negative_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=2, dollar_per_point=-1.0)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_DOLLAR_PER_POINT"

    def test_monetary_dpp_nan_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=2, dollar_per_point=float("nan"))
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_DOLLAR_PER_POINT"

    def test_monetary_dpp_inf_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=2, dollar_per_point=float("inf"))
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_DOLLAR_PER_POINT"

    def test_monetary_dpp_bool_rejected(self):
        cfg = ConversionConfig(pnl_units="monetary", quantity=2, dollar_per_point=True)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_DOLLAR_PER_POINT"

    def test_monetary_both_negative_product_positive_rejected(self):
        """quantity=-1, dpp=-2 â†’ product=2 positive, pero ambos invÃ¡lidos."""
        cfg = ConversionConfig(pnl_units="monetary", quantity=-1, dollar_per_point=-2.0)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        codes = [e.code for e in result.errors]
        assert "INVALID_QUANTITY" in codes

    def test_costs_not_double_counted(self):
        """pnl_net already includes costs; adapter does not subtract again."""
        rec = _base_record(pnl_net=8, entry=100, stop=95)  # R = 8/5 = 1.6
        result = convert_trades([rec])
        assert result.trades[0].r_result == pytest.approx(1.6)
        assert result.trades[0].metadata["original_pnl_net"] == 8

    def test_metadata_preserves_original_pnl(self):
        rec = _base_record(pnl_net=42)
        result = convert_trades([rec])
        assert result.trades[0].metadata["original_pnl_net"] == 42


# ===========================================================================
# 3. Riesgo cero, stop mal orientado, NaN/inf, side invÃ¡lido
# ===========================================================================

class TestValidationErrors:
    def test_zero_risk(self):
        rec = _base_record(entry=100, stop=100, pnl_net=5)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "ZERO_OR_NON_FINITE_RISK"

    def test_stop_misplaced_long(self):
        rec = _base_record(side=1, entry=100, stop=105, pnl_net=5)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "STOP_MISPLACED_LONG"

    def test_stop_misplaced_short(self):
        rec = _base_record(side=-1, entry=100, stop=95, pnl_net=5)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "STOP_MISPLACED_SHORT"

    def test_nan_entry(self):
        rec = _base_record(entry=float("nan"))
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "NON_FINITE_ENTRY"

    def test_inf_pnl(self):
        rec = _base_record(pnl_net=float("inf"))
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "NON_FINITE_PNL_NET"

    def test_side_zero(self):
        rec = _base_record(side=0)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "SIDE_ZERO"

    def test_side_one_point_five(self):
        rec = _base_record(side=1.5)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "SIDE_NOT_INTEGER"

    def test_side_two(self):
        rec = _base_record(side=2)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "SIDE_OUT_OF_RANGE"

    def test_side_bool_rejected(self):
        rec = _base_record(side=True)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_SIDE_TYPE"

    def test_missing_entry(self):
        rec = _base_record()
        del rec["entry"]
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_ENTRY"

    def test_non_numeric_stop(self):
        rec = _base_record(stop="abc")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "NON_NUMERIC_STOP"


# ===========================================================================
# 4. SÃ­mbolos: sin alias entre contratos, normalizaciÃ³n solo espacios/case,
#    comparaciÃ³n asset vs symbol, sÃ­mbolo por registro, lotes mixtos,
#    fila invÃ¡lida no condiciona las siguientes
# ===========================================================================

class TestSymbol:
    def test_mnq_valid(self):
        """MNQ se acepta y se conserva tal cual."""
        rec = _base_record(asset="MNQ")
        result = convert_trades([rec])
        assert result.symbol_used == "MNQ"
        assert result.trades[0].asset == "MNQ"

    def test_ym_stays_ym_no_alias(self):
        """YM no se convierte a MYM; son contratos distintos."""
        rec = _base_record(asset="YM")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.symbol_used == "YM"
        assert result.trades[0].asset == "YM"

    def test_nq_stays_nq_no_alias(self):
        """NQ no se convierte a MNQ; son contratos distintos."""
        rec = _base_record(asset="NQ")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.symbol_used == "NQ"
        assert result.trades[0].asset == "NQ"

    def test_es_stays_es_no_alias(self):
        """ES no se convierte a MES."""
        rec = _base_record(asset="ES")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.symbol_used == "ES"
        assert result.trades[0].asset == "ES"

    def test_gc_stays_gc_no_alias(self):
        """GC no se convierte a MGC."""
        rec = _base_record(asset="GC")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.symbol_used == "GC"
        assert result.trades[0].asset == "GC"

    def test_normalize_spaces_and_case(self):
        """Solo se normalizan espacios y mayÃºsculas."""
        rec = _base_record(asset="  mnq  ")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.symbol_used == "MNQ"
        assert result.trades[0].asset == "MNQ"

    def test_asset_vs_symbol_mismatch_rejected(self):
        """Si un registro trae asset y symbol diferentes, se rechaza."""
        rec = _base_record(asset="MNQ", symbol="YM")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "SYMBOL_MISMATCH"

    def test_asset_vs_symbol_match_accepted(self):
        """Si asset y symbol coinciden, se acepta."""
        rec = _base_record(asset="MNQ", symbol="MNQ")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.symbol_used == "MNQ"

    def test_symbol_vs_config_mismatch_rejected(self):
        """Si symbol difiere de config.symbol, se rechaza."""
        cfg = ConversionConfig(symbol="MNQ")
        rec = _base_record()
        del rec["asset"]
        rec["symbol"] = "YM"
        result = convert_trades([rec], config=cfg)
        assert result.rejected == 1
        assert result.errors[0].code == "SYMBOL_CONFLICT"

    def test_symbol_vs_config_match_accepted(self):
        """Si symbol coincide con config.symbol, se acepta."""
        cfg = ConversionConfig(symbol="MNQ")
        rec = _base_record(symbol="MNQ")
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1

    def test_no_symbol_no_config_rejected(self):
        """Sin config.symbol y sin asset/symbol en el registro â†’ rechazado."""
        rec = _base_record()
        del rec["asset"]
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_SYMBOL"

    def test_mixed_batch_symbol_used_none(self):
        """Lote mixto MNQ+YM sin config â†’ ambos aceptados, symbol_used=None."""
        r1 = _base_record(asset="MNQ", trade_id="MX-1")
        r2 = _base_record(asset="YM", trade_id="MX-2")
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        assert result.symbol_used is None
        assert result.trades[0].asset == "MNQ"
        assert result.trades[1].asset == "YM"

    def test_no_inherit_symbol_from_previous_row(self):
        """El sÃ­mbolo de una fila no se hereda a la siguiente."""
        r1 = _base_record(asset="MNQ", trade_id="INH-1")
        r2 = _base_record(trade_id="INH-2")
        del r2["asset"]
        result = convert_trades([r1, r2])
        assert result.accepted == 1
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_SYMBOL"
        assert result.errors[0].record_index == 1

    def test_invalid_row_does_not_block_next(self):
        """Una fila con sÃ­mbolo invÃ¡lido no impide aceptar la siguiente."""
        r1 = _base_record(asset="BTC")
        r2 = _base_record(asset="MNQ")
        result = convert_trades([r1, r2])
        assert result.accepted == 1
        assert result.rejected == 1
        assert result.trades[0].asset == "MNQ"

    def test_config_symbol_applied_when_record_has_none(self):
        """Sin asset/symbol en registro, config.symbol se usa."""
        cfg = ConversionConfig(symbol="YM")
        rec = _base_record()
        del rec["asset"]
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        assert result.symbol_used == "YM"
        assert result.trades[0].asset == "YM"

    def test_config_symbol_rejects_record_symbol_conflict(self):
        """Si config.symbol=Y but record.asset=MNQ â†’ SYMBOL_CONFLICT."""
        cfg = ConversionConfig(symbol="YM")
        rec = _base_record(asset="MNQ")
        result = convert_trades([rec], config=cfg)
        assert result.rejected == 1
        assert result.errors[0].code == "SYMBOL_CONFLICT"

    def test_invalid_symbol(self):
        rec = _base_record(asset="BTC")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_SYMBOL"

    def test_asset_non_string_structured_error(self):
        """asset=123 â†’ INVALID_SYMBOL_TYPE, no str() conversion."""
        rec = _base_record(asset=123)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_SYMBOL_TYPE"
        assert result.accepted == 0

    def test_symbol_non_string_structured_error(self):
        """symbol=123 â†’ INVALID_SYMBOL_TYPE."""
        rec = _base_record(symbol=123)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_SYMBOL_TYPE"

    def test_config_symbol_non_string_rejected(self):
        """config.symbol=123 â†’ INVALID_CONFIG_SYMBOL_TYPE, retorno inmediato."""
        cfg = ConversionConfig(symbol=123)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_CONFIG_SYMBOL_TYPE"

    def test_config_symbol_overrides(self):
        cfg = ConversionConfig(symbol="MNQ")
        rec = _base_record()
        del rec["asset"]
        result = convert_trades([rec], config=cfg)
        assert result.symbol_used == "MNQ"
        assert result.trades[0].asset == "MNQ"


# ===========================================================================
# 5. Timestamps: source_timezone, analysis_timezone, DST, precisiÃ³n
# ===========================================================================

class TestTimestamps:
    # --- Requisito 1: fechas ausentes/vacÃ­as/invÃ¡lidas/naive sin zona â†’ rechazo ---

    def test_naive_without_source_timezone_rejected(self):
        """Naive sin source_timezone â†’ timestamp=None, date vacÃ­o â†’ rechazado."""
        rec = _base_record(t="2024-06-01T10:00:00")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_TIMESTAMP"

    def test_empty_timestamp_rejected(self):
        """Timestamp vacÃ­o â†’ rechazado."""
        rec = _base_record(t="")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_TIMESTAMP"

    def test_invalid_timestamp_rejected(self):
        """Timestamp con formato invÃ¡lido â†’ rechazado."""
        rec = _base_record(t="not-a-date")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_TIMESTAMP"

    def test_none_timestamp_rejected(self):
        """Timestamp None â†’ rechazado."""
        rec = _base_record()
        del rec["t"]
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_TIMESTAMP"

    # --- Requisito 2: source_timezone separada de analysis_timezone ---

    def test_source_timezone_interprets_naive(self):
        """source_timezone interpreta naive; analysis_timezone no."""
        rec = _base_record(t="2024-06-15T10:00:00")
        cfg = ConversionConfig(source_timezone="America/New_York")
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        ts = result.trades[0].timestamp
        assert ts is not None
        # 10:00 EDT = 14:00 UTC
        assert ts.hour == 14
        assert ts.tzinfo is not None

    def test_analysis_timezone_does_not_interpret_naive(self):
        """analysis_timezone sola NO interpreta naive â†’ rechazado."""
        rec = _base_record(t="2024-06-15T10:00:00")
        cfg = ConversionConfig(analysis_timezone="America/New_York")
        result = convert_trades([rec], config=cfg)
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_TIMESTAMP"

    # --- Requisito 3: normalizaciÃ³n a UTC ---

    def test_utc_timestamp(self):
        rec = _base_record(t="2024-06-01T10:00:00+00:00")
        result = convert_trades([rec])
        ts = result.trades[0].timestamp
        assert ts is not None
        assert ts.tzinfo is not None
        assert ts.utcoffset().total_seconds() == 0

    def test_explicit_offset_normalized_to_utc(self):
        """-05:00 â†’ hora UTC correcta."""
        rec = _base_record(t="2024-06-01T10:00:00-05:00")
        result = convert_trades([rec])
        ts = result.trades[0].timestamp
        assert ts is not None
        assert ts.hour == 15  # 10:00 EST = 15:00 UTC

    # --- Requisito 4: rechaza DST ambiguo/inexistente ---

    def test_ambiguous_dst_naive_rejected(self):
        """2024-11-03T01:30:00 naive en NY â†’ hora ambigua â†’ rechazado."""
        rec = _base_record(t="2024-11-03T01:30:00")
        cfg = ConversionConfig(source_timezone="America/New_York")
        result = convert_trades([rec], config=cfg)
        assert result.rejected == 1
        assert result.errors[0].code == "AMBIGUOUS_TIMESTAMP"

    def test_nonexistent_dst_naive_rejected(self):
        """2024-03-10T02:30:00 naive en NY â†’ hora inexistente â†’ rechazado."""
        rec = _base_record(t="2024-03-10T02:30:00")
        cfg = ConversionConfig(source_timezone="America/New_York")
        result = convert_trades([rec], config=cfg)
        assert result.rejected == 1
        assert result.errors[0].code == "NONEXISTENT_TIMESTAMP"

    # --- Requisito 5: fecha local normal con source_timezone â†’ aceptada sin warning ---

    def test_normal_local_with_source_timezone_no_warning(self):
        """2024-06-15T10:00:00 con source_timezone=NY â†’ aceptado, sin warnings."""
        rec = _base_record(t="2024-06-15T10:00:00")
        cfg = ConversionConfig(source_timezone="America/New_York")
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        assert len(result.warnings) == 0

    # --- Requisito 6: offset explÃ­cito produce UTC correcto ---

    def test_explicit_offset_edt_produces_correct_utc(self):
        """2024-11-03T01:30:00-04:00 â†’ 05:30 UTC."""
        rec = _base_record(t="2024-11-03T01:30:00-04:00")
        result = convert_trades([rec])
        ts = result.trades[0].timestamp
        assert ts is not None
        assert ts.hour == 5
        assert ts.minute == 30

    def test_explicit_offset_est_produces_correct_utc(self):
        """2024-11-03T01:30:00-05:00 â†’ 06:30 UTC."""
        rec = _base_record(t="2024-11-03T01:30:00-05:00")
        result = convert_trades([rec])
        ts = result.trades[0].timestamp
        assert ts is not None
        assert ts.hour == 6
        assert ts.minute == 30

    # --- Requisito 7: no replace(tzinfo=...) â€” validaciÃ³n con fold ---

    def test_source_timezone_uses_fold_disambiguation(self):
        """source_timezone con hora ambigua usa fold=1 (post-transition)."""
        rec = _base_record(t="2024-11-03T01:30:00")
        cfg = ConversionConfig(
            source_timezone="America/New_York",
            ambiguous_timestamp_fold=1,
        )
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        ts = result.trades[0].timestamp
        # fold=1 â†’ post-transition EST â†’ 06:30 UTC
        assert ts.hour == 6
        assert ts.minute == 30

    # --- Requisito 8: precisiÃ³n soportada ---

    def test_microsecond_precision_preserved(self):
        """PrecisiÃ³n de 6 dÃ­gitos fraccionarios se preserva exactamente."""
        rec = _base_record(t="2024-06-15T10:00:00.123456+00:00")
        result = convert_trades([rec])
        ts = result.trades[0].timestamp
        assert ts is not None
        assert ts.microsecond == 123456

    def test_six_digits_z_suffix_preserved(self):
        """Z suffix con 6 dÃ­gitos se preserva."""
        rec = _base_record(t="2024-06-15T10:00:00.654321Z")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.trades[0].timestamp.microsecond == 654321

    def test_six_digits_bare_naive_preserved(self):
        """6 dÃ­gitos sin offset se preserva (requiere source_timezone)."""
        from src.tsfm_adapter import ConversionConfig
        rec = _base_record(t="2024-06-15T10:00:00.111111")
        cfg = ConversionConfig(source_timezone="UTC")
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        assert result.trades[0].timestamp.microsecond == 111111

    def test_seven_digits_rejected(self):
        """7 dÃ­gitos fraccionarios â†’ trunca silenciosamente â†’ rechazado."""
        rec = _base_record(t="2024-06-15T10:00:00.1234567+00:00")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "UNSUPPORTED_TIMESTAMP_PRECISION"

    def test_nine_digits_rejected(self):
        """9 dÃ­gitos fraccionarios â†’ rechazado."""
        rec = _base_record(t="2024-06-15T10:00:00.123456789+00:00")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "UNSUPPORTED_TIMESTAMP_PRECISION"

    # --- Requisito 9: fila invÃ¡lida no bloquea la siguiente ---

    def test_invalid_timestamp_does_not_block_next(self):
        """Timestamp invÃ¡lido en fila 0 no impide aceptar fila 1."""
        r1 = _base_record(t="not-a-date", trade_id="TS-1")
        r2 = _base_record(t="2024-06-01T10:00:00+00:00", trade_id="TS-2")
        result = convert_trades([r1, r2])
        assert result.accepted == 1
        assert result.rejected == 1
        assert result.trades[0].timestamp is not None

    # --- date poblado desde UTC ---

    def test_date_populated_from_utc(self):
        rec = _base_record(t="2024-01-15T23:30:00+00:00")
        result = convert_trades([rec])
        assert result.trades[0].date == "2024-01-15"

    def test_date_never_empty(self):
        """Un trade aceptado nunca tiene date vacÃ­o."""
        rec = _base_record(t="2024-06-15T10:00:00+00:00")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.trades[0].date != ""

    def test_date_uses_utc_when_no_analysis_timezone(self):
        """Sin analysis_timezone, date se deriva de UTC."""
        rec = _base_record(t="2024-06-15T23:30:00+00:00")
        result = convert_trades([rec])
        # 23:30 UTC on June 15 â†’ date = 2024-06-15
        assert result.trades[0].date == "2024-06-15"
        assert result.trades[0].metadata.get("analysis_timezone") == "UTC"

    def test_date_uses_analysis_timezone_when_provided(self):
        """Con analysis_timezone, date se deriva en esa zona."""
        from src.tsfm_adapter import ConversionConfig
        rec = _base_record(t="2024-06-15T23:30:00+00:00")
        cfg = ConversionConfig(analysis_timezone="America/New_York")
        result = convert_trades([rec], config=cfg)
        # 23:30 UTC = 19:30 EDT on June 15 â†’ date = 2024-06-15
        assert result.trades[0].date == "2024-06-15"
        assert result.trades[0].metadata.get("analysis_timezone") == "America/New_York"

    def test_midnight_boundary_different_dates_utc_vs_ny(self):
        """Instante cercano a medianoche produce fechas distintas en UTC vs NY."""
        from src.tsfm_adapter import ConversionConfig
        # 2024-06-16T00:30:00+00:00 UTC = 2024-06-15T20:30:00 EDT
        rec_utc = _base_record(t="2024-06-16T00:30:00+00:00", trade_id="BD-UTC")
        rec_ny = _base_record(t="2024-06-16T00:30:00+00:00", trade_id="BD-NY")
        r_utc = convert_trades([rec_utc])
        r_ny = convert_trades([rec_ny], config=ConversionConfig(analysis_timezone="America/New_York"))
        assert r_utc.trades[0].date == "2024-06-16"
        assert r_ny.trades[0].date == "2024-06-15"


# ===========================================================================
# 6. Outcomes incompletos y declaraciÃ³n de finalizaciÃ³n ausente
# ===========================================================================

class TestOutcomes:
    # --- NormalizaciÃ³n ---
    def test_closed_accepted(self):
        rec = _base_record(outcome="closed")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.trades[0].metadata["outcome"] == "closed"

    def test_uppercase_normalized(self):
        """'CLOSED' â†’ normalizado a 'closed'."""
        rec = _base_record(outcome="CLOSED")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.trades[0].metadata["outcome"] == "closed"

    def test_whitespace_stripped(self):
        """'  closed  ' â†’ normalizado a 'closed'."""
        rec = _base_record(outcome="  closed  ")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.trades[0].metadata["outcome"] == "closed"

    # --- Rechazos ---
    def test_empty_string_rejected(self):
        rec = _base_record(outcome="")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_OUTCOME"

    def test_whitespace_only_rejected(self):
        rec = _base_record(outcome="   ")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_OUTCOME"

    def test_non_string_rejected(self):
        rec = _base_record(outcome=123)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_OUTCOME_TYPE"

    def test_unknown_outcome_rejected(self):
        rec = _base_record(outcome="filled_partial")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "UNKNOWN_OUTCOME"

    def test_pending_rejected(self):
        rec = _base_record(outcome="pending")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "UNFINALISED_OUTCOME"

    def test_no_fill_rejected(self):
        rec = _base_record(outcome="no_fill")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "UNFINALISED_OUTCOME"

    def test_unresolved_rejected(self):
        rec = _base_record(outcome="unresolved")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "UNFINALISED_OUTCOME"

    def test_partial_rejected(self):
        rec = _base_record(outcome="partial")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "UNFINALISED_OUTCOME"

    def test_open_rejected(self):
        rec = _base_record(outcome="open")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "UNFINALISED_OUTCOME"

    def test_unfinalised_never_accepted_by_config(self):
        """Un estado no final nunca se acepta, sin importar config."""
        cfg = ConversionConfig(outcomes_finalized=True, source_dataset="test")
        rec = _base_record(outcome="pending")
        result = convert_trades([rec], config=cfg)
        assert result.rejected == 1
        assert result.errors[0].code == "UNFINALISED_OUTCOME"

    # --- Allowlist personalizada ---
    def test_custom_allowlist_accepted(self):
        cfg = ConversionConfig(allowed_outcomes=frozenset({"closed", "expired"}))
        rec = _base_record(outcome="expired")
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1

    def test_custom_allowlist_rejects_default(self):
        """'closed'not in custom allowlist â†’ rechazado."""
        cfg = ConversionConfig(allowed_outcomes=frozenset({"expired"}))
        rec = _base_record(outcome="closed")
        result = convert_trades([rec], config=cfg)
        assert result.rejected == 1
        assert result.errors[0].code == "UNKNOWN_OUTCOME"

    # --- Outcome ausente ---
    def test_missing_outcome_no_declaration_rejected(self):
        """Sin outcome y sin outcomes_finalized â†’ rechazado."""
        rec = _base_record()
        del rec["outcome"]
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_OUTCOME"

    def test_missing_outcome_no_source_dataset_rejected(self):
        """outcomes_finalized=True pero sin source_dataset â†’ rechazado."""
        cfg = ConversionConfig(outcomes_finalized=True)
        rec = _base_record()
        del rec["outcome"]
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "MISSING_SOURCE_DATASET"

    def test_missing_outcome_with_declaration_and_dataset_accepted(self):
        """outcomes_finalized=True + source_dataset â†’ aceptado sin outcome."""
        cfg = ConversionConfig(outcomes_finalized=True, source_dataset="prod_v2")
        rec = _base_record()
        del rec["outcome"]
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        m = result.trades[0].metadata
        assert m["outcome"] is None
        assert m["source_dataset"] == "prod_v2"
        assert m["outcomes_finalized"] is True

    # --- accept_partial_outcomes eliminado ---
    def test_accept_partial_outcomes_removed(self):
        """accept_partial_outcomes ya no existe â†’ TypeError."""
        with pytest.raises(TypeError):
            ConversionConfig(accept_partial_outcomes=True)

    # --- Fila invÃ¡lida no bloquea siguiente ---
    def test_invalid_outcome_does_not_block_next(self):
        r1 = _base_record(outcome="pending", trade_id="OC-1")
        r2 = _base_record(outcome="closed", trade_id="OC-2")
        result = convert_trades([r1, r2])
        assert result.accepted == 1
        assert result.rejected == 1
        assert result.trades[0].trade_id != ""


# ===========================================================================
# 7. Identidad estable al reordenar registros
# ===========================================================================
# 7. Identidad estable (C3-A): namespace, source_id, fingerprint
# ===========================================================================

class TestIdentity:
    def test_stable_id_on_reorder(self):
        r1 = _base_record(t="2024-01-01T10:00:00+00:00", entry=100)
        r2 = _base_record(t="2024-01-01T11:00:00+00:00", entry=200)
        result_a = convert_trades([r1, r2])
        result_b = convert_trades([r2, r1])
        ids_a = [t.trade_id for t in result_a.trades]
        ids_b = [t.trade_id for t in result_b.trades]
        assert set(ids_a) == set(ids_b)

    def test_source_id_from_trade_id(self):
        rec = _base_record(trade_id="BROKER-42")
        result = convert_trades([rec], config=ConversionConfig(namespace="tsfm"))
        assert len(result.trades[0].trade_id) == 64  # SHA-256 hex

    def test_source_id_from_id_field(self):
        rec = _base_record()
        if "trade_id" in rec:
            del rec["trade_id"]
        rec["id"] = "ALT-99"
        result = convert_trades([rec])
        assert len(result.trades[0].trade_id) == 64

    def test_both_source_ids_same_accepted(self):
        rec = _base_record(trade_id="BROKER-5", id="BROKER-5")
        result = convert_trades([rec])
        assert result.accepted == 1
        assert len(result.trades[0].trade_id) == 64

    def test_both_source_ids_differ_rejected(self):
        rec = _base_record(trade_id="A", id="B")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "SOURCE_ID_MISMATCH"

    def test_source_id_numeric_rejected(self):
        rec = _base_record(trade_id=42)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_SOURCE_ID_TYPE"

    def test_source_id_bool_rejected(self):
        rec = _base_record(trade_id=True)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_SOURCE_ID_TYPE"

    def test_source_id_empty_rejected(self):
        rec = _base_record(trade_id="  ")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "EMPTY_SOURCE_ID"

    def test_namespace_prefixed(self):
        rec = _base_record()
        cfg = ConversionConfig(namespace="my_ns")
        result = convert_trades([rec], config=cfg)
        assert len(result.trades[0].trade_id) == 64

    def test_namespace_separates_ids(self):
        rec = _base_record()
        r1 = convert_trades([rec], config=ConversionConfig(namespace="ns1"))
        r2 = convert_trades([rec], config=ConversionConfig(namespace="ns2"))
        assert r1.trades[0].trade_id != r2.trades[0].trade_id

    def test_namespace_must_be_string(self):
        cfg = ConversionConfig(namespace=123)
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "INVALID_NAMESPACE_TYPE"

    def test_namespace_empty_rejected(self):
        cfg = ConversionConfig(namespace="   ")
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert len(result.errors) >= 1
        assert result.errors[0].code == "EMPTY_NAMESPACE"

    def test_content_fingerprint_present_in_metadata(self):
        rec = _base_record()
        result = convert_trades([rec])
        assert result.accepted == 1
        meta = result.trades[0].metadata
        assert "content_fingerprint" in meta
        assert isinstance(meta["content_fingerprint"], str)
        assert len(meta["content_fingerprint"]) > 0

    def test_identity_schema_version_present(self):
        rec = _base_record()
        result = convert_trades([rec])
        meta = result.trades[0].metadata
        assert meta.get("identity_schema_version") == "v2"

    def test_fingerprint_same_for_source_id_records(self):
        rec = _base_record(trade_id="BROK-1")
        result = convert_trades([rec])
        meta = result.trades[0].metadata
        assert "content_fingerprint" in meta
        assert len(meta["content_fingerprint"]) == 64

    def test_equivalent_numbers_same_fingerprint(self):
        r1 = _base_record(entry=100, trade_id="N1")
        r2 = _base_record(entry=100.0, trade_id="N2")
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        fp1 = result.trades[0].metadata["content_fingerprint"]
        fp2 = result.trades[1].metadata["content_fingerprint"]
        assert fp1 == fp2

    def test_negative_zero_normalized(self):
        r1 = _base_record(side=-1, entry=100.0, stop=105.0, trade_id="Z1")
        r2 = _base_record(side=-1, entry=100.0, stop=105.0, trade_id="Z2")
        # Now vary pnl_net with 0.0 vs -0.0 to test canonical normalization
        r1["pnl_net"] = 0.0
        r2["pnl_net"] = -0.0
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        fp1 = result.trades[0].metadata["content_fingerprint"]
        fp2 = result.trades[1].metadata["content_fingerprint"]
        assert fp1 == fp2

    def test_equivalent_timestamps_same_fingerprint(self):
        r1 = _base_record(t="2024-06-01T10:00:00+00:00", trade_id="T1")
        r2 = _base_record(t="2024-06-01T06:00:00-04:00", trade_id="T2")
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        fp1 = result.trades[0].metadata["content_fingerprint"]
        fp2 = result.trades[1].metadata["content_fingerprint"]
        assert fp1 == fp2

    def test_entry_change_alters_fingerprint(self):
        r1 = _base_record(entry=100, trade_id="C1")
        r2 = _base_record(entry=101, trade_id="C2")
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        fp1 = result.trades[0].metadata["content_fingerprint"]
        fp2 = result.trades[1].metadata["content_fingerprint"]
        assert fp1 != fp2

    def test_direction_change_alters_fingerprint(self):
        r1 = _base_record(side=1, entry=100, stop=95, trade_id="D1")
        r2 = _base_record(side=-1, entry=100, stop=105, trade_id="D2")
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        fp1 = result.trades[0].metadata["content_fingerprint"]
        fp2 = result.trades[1].metadata["content_fingerprint"]
        assert fp1 != fp2

    def test_outcome_change_alters_fingerprint(self):
        r1 = _base_record(outcome="closed", trade_id="O1")
        cfg_c = ConversionConfig(allowed_outcomes=frozenset({"closed", "expired"}))
        r2 = _base_record(outcome="expired", trade_id="O2")
        result = convert_trades([r1, r2], config=cfg_c)
        assert result.accepted == 2
        fp1 = result.trades[0].metadata["content_fingerprint"]
        fp2 = result.trades[1].metadata["content_fingerprint"]
        assert fp1 != fp2

    def test_source_id_stable_though_content_changes(self):
        """Same source_id with different content: identity matches, fingerprint differs."""
        r1 = _base_record(side=1, entry=100, stop=95, trade_id="SID-1")
        r2 = _base_record(side=1, entry=999, stop=994, trade_id="SID-1")
        res1 = convert_trades([r1])
        res2 = convert_trades([r2])
        assert res1.accepted == 1
        assert res2.accepted == 1
        assert res1.trades[0].trade_id == res2.trades[0].trade_id
        assert res1.trades[0].metadata["content_fingerprint"] != res2.trades[0].metadata["content_fingerprint"]

    def test_namespace_with_colon_no_collision(self):
        """ns:sub with id=X differs from ns with id=sub:X (no delimiter ambiguity)."""
        cfg1 = ConversionConfig(namespace="ns:sub")
        cfg2 = ConversionConfig(namespace="ns")
        r1 = _base_record(trade_id="X")
        r2 = _base_record(trade_id="sub:X")
        res1 = convert_trades([r1], config=cfg1)
        res2 = convert_trades([r2], config=cfg2)
        assert res1.accepted == 1
        assert res2.accepted == 1
        assert res1.trades[0].trade_id != res2.trades[0].trade_id

    def test_source_id_with_colon_no_collision(self):
        """Source IDs with colons don't collide with namespace splits."""
        r1 = _base_record(trade_id="a:b:c")
        r2 = _base_record(trade_id="d:e:f")
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        assert result.trades[0].trade_id != result.trades[1].trade_id

    def test_strategy_must_be_string(self):
        rec = _base_record(strategy=42)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_STRATEGY_TYPE"

    def test_strategy_bool_rejected(self):
        rec = _base_record(strategy=True)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_STRATEGY_TYPE"

    def test_strategy_empty_rejected(self):
        rec = _base_record(strategy="")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "EMPTY_STRATEGY"

    def test_strategy_whitespace_rejected(self):
        rec = _base_record(strategy="   ")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "EMPTY_STRATEGY"

    def test_identity_invalid_row_does_not_block_next(self):
        r1 = _base_record(trade_id=123)
        r2 = _base_record(trade_id="GOOD-1")
        result = convert_trades([r1, r2])
        assert result.accepted == 1
        assert result.rejected == 1



    # --- Collision: ns:sub/X vs ns/sub:X ---
    def test_ns_sub_x_vs_ns_subx_different_identities(self):
        """ns:sub with id=X must differ from ns with id=sub:X."""
        cfg_a = ConversionConfig(namespace="ns:sub")
        cfg_b = ConversionConfig(namespace="ns")
        r_a = _base_record(trade_id="X")
        r_b = _base_record(trade_id="sub:X")
        res_a = convert_trades([r_a], config=cfg_a)
        res_b = convert_trades([r_b], config=cfg_b)
        assert res_a.accepted == 1 and res_b.accepted == 1
        assert res_a.trades[0].trade_id != res_b.trades[0].trade_id

    # --- Source ID that matches a content fingerprint ---
    def test_source_id_matching_fingerprint_differs(self):
        """A source_id that happens to equal a content fingerprint produces
        a different identity than a content-derived identity without source_id."""
        r_no_id = _base_record(entry=100)
        r_no_id.pop("trade_id", None)
        res_no = convert_trades([r_no_id])
        # Use the content_fingerprint from the no-id record as a source_id
        fp = res_no.trades[0].metadata["content_fingerprint"]
        r_with_id = _base_record(entry=100, trade_id=fp)
        res_with = convert_trades([r_with_id])
        assert res_no.accepted == 1 and res_with.accepted == 1
        assert res_no.trades[0].trade_id != res_with.trades[0].trade_id

    # --- Namespace whitespace equivalence ---
    def test_namespace_whitespace_equivalence(self):
        """ ns with interior spaces equals its stripped version."""
        cfg1 = ConversionConfig(namespace="  tsfm  ")
        cfg2 = ConversionConfig(namespace="tsfm")
        rec = _base_record()
        res1 = convert_trades([rec], config=cfg1)
        res2 = convert_trades([rec], config=cfg2)
        assert res1.accepted == 1 and res2.accepted == 1
        assert res1.trades[0].trade_id == res2.trades[0].trade_id
        assert res1.trades[0].metadata["namespace"] == "tsfm"
        assert res2.trades[0].metadata["namespace"] == "tsfm"

    # --- Strategy: None and missing rejected ---
    def test_strategy_none_rejected(self):
        rec = _base_record(strategy=None)
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_STRATEGY"

    def test_strategy_missing_key_rejected(self):
        rec = _base_record()
        del rec["strategy"]
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "MISSING_STRATEGY"

    # --- Strategy: strip equivalence in fingerprint ---
    def test_strategy_strip_equivalence_same_fingerprint(self):
        r1 = _base_record(strategy="momentum", trade_id="S1")
        r2 = _base_record(strategy="  momentum  ", trade_id="S2")
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        fp1 = result.trades[0].metadata["content_fingerprint"]
        fp2 = result.trades[1].metadata["content_fingerprint"]
        assert fp1 == fp2
        assert result.trades[0].strategy == "momentum"
        assert result.trades[1].strategy == "momentum"

    def test_strategy_strip_equivalence_same_trade_id(self):
        """Same effective strategy after strip -> same identity when content matches."""
        r1 = _base_record(strategy="momentum", trade_id="S1")
        r2 = _base_record(strategy="  momentum  ", trade_id="S2")
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        assert result.trades[0].trade_id != result.trades[1].trade_id
        # But fingerprints match (strategy normalized)
        assert result.trades[0].metadata["content_fingerprint"] == result.trades[1].metadata["content_fingerprint"]
# ===========================================================================
# 8. Duplicados y colisiones de identidad
# ===========================================================================

class TestDuplicates:
    # --- Rule A: same source_id + same fingerprint -> deduplicate ---
    def test_two_copies_deduplicated(self):
        r1 = _base_record(trade_id="X")
        r2 = _base_record(trade_id="X")
        result = convert_trades([r1, r2])
        assert result.accepted == 1
        assert result.deduplicated == 1
        assert result.rejected == 0
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "DEDUPLICATED"
        assert result.warnings[0].record_index == 1

    def test_three_copies_deduplicated(self):
        r1 = _base_record(trade_id="X")
        r2 = _base_record(trade_id="X")
        r3 = _base_record(trade_id="X")
        result = convert_trades([r1, r2, r3])
        assert result.accepted == 1
        assert result.deduplicated == 2
        assert result.rejected == 0
        assert len(result.warnings) == 2

    def test_deduplication_order_independent(self):
        """Deduplication result is the same regardless of record order."""
        r1 = _base_record(trade_id="X", entry=100)
        r2 = _base_record(trade_id="X", entry=100)
        res_ab = convert_trades([r1, r2])
        res_ba = convert_trades([r2, r1])
        assert res_ab.accepted == res_ba.accepted == 1
        assert res_ab.deduplicated == res_ba.deduplicated == 1
        # Same identity produced
        assert res_ab.trades[0].trade_id == res_ba.trades[0].trade_id

    # --- Rule B: same source_id + different fingerprints -> reject ALL ---
    def test_source_id_content_conflict_rejects_all(self):
        r1 = _base_record(trade_id="X", entry=100)
        r2 = _base_record(trade_id="X", entry=200)
        result = convert_trades([r1, r2])
        assert result.accepted == 0
        assert result.rejected == 2
        assert result.deduplicated == 0
        assert all(e.code == "SOURCE_ID_CONTENT_CONFLICT" for e in result.errors)

    def test_aba_conflict_rejects_all_three(self):
        """Two copies of A and one B -> all three rejected."""
        r1 = _base_record(trade_id="X", entry=100)
        r2 = _base_record(trade_id="X", entry=100)
        r3 = _base_record(trade_id="X", entry=200)
        result = convert_trades([r1, r2, r3])
        assert result.accepted == 0
        assert result.rejected == 3
        assert result.deduplicated == 0

    def test_conflict_order_independent(self):
        """Conflict result is the same regardless of record order."""
        r1 = _base_record(trade_id="X", entry=100)
        r2 = _base_record(trade_id="X", entry=200)
        res_ab = convert_trades([r1, r2])
        res_ba = convert_trades([r2, r1])
        assert res_ab.accepted == res_ba.accepted == 0
        assert res_ab.rejected == res_ba.rejected == 2

    # --- Rule C: no source_id + same fingerprint -> reject all ambiguous ---
    def test_no_id_same_content_ambiguous(self):
        r1 = _base_record(t="2024-01-01T10:00:00+00:00", entry=100)
        r2 = _base_record(t="2024-01-01T10:00:00+00:00", entry=100)
        r1.pop("trade_id", None)
        r2.pop("trade_id", None)
        result = convert_trades([r1, r2])
        assert result.accepted == 0
        assert result.rejected == 2
        assert result.deduplicated == 0
        assert all(e.code == "AMBIGUOUS_DUPLICATE_WITHOUT_SOURCE_ID" for e in result.errors)

    def test_no_id_three_ambiguous(self):
        r1 = _base_record(t="2024-01-01T10:00:00+00:00", entry=100)
        r2 = _base_record(t="2024-01-01T10:00:00+00:00", entry=100)
        r3 = _base_record(t="2024-01-01T10:00:00+00:00", entry=100)
        r1.pop("trade_id", None)
        r2.pop("trade_id", None)
        r3.pop("trade_id", None)
        result = convert_trades([r1, r2, r3])
        assert result.accepted == 0
        assert result.rejected == 3
        assert result.deduplicated == 0

    # --- Rule D: different source_ids + same fingerprint -> keep both ---
    def test_different_ids_same_content_accepted(self):
        r1 = _base_record(trade_id="A", pnl_net=10)
        r2 = _base_record(trade_id="B", pnl_net=10)
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        assert result.rejected == 0

    # --- Rule E: no source_id + different fingerprints -> keep both ---
    def test_no_id_different_content_accepted(self):
        r1 = _base_record(t="2024-01-01T10:00:00+00:00", entry=100)
        r2 = _base_record(t="2024-01-01T10:00:00+00:00", entry=200)
        r1.pop("trade_id", None)
        r2.pop("trade_id", None)
        result = convert_trades([r1, r2])
        assert result.accepted == 2
        assert result.rejected == 0
        assert result.trades[0].trade_id != result.trades[1].trade_id

    # --- Counts: accepted + rejected + deduplicated == total ---
    def test_counts_consistent(self):
        r1 = _base_record(trade_id="X")
        r2 = _base_record(trade_id="X")
        result = convert_trades([r1, r2])
        assert result.accepted + result.rejected + result.deduplicated == result.total_records

    # --- Mixed batch with valid, copies, conflicts, and errors ---
    def test_mixed_batch_exact_counts(self):
        r_good1 = _base_record(trade_id="G1", entry=100)       # valid
        r_good2 = _base_record(trade_id="G2", entry=101)       # valid
        r_copy1 = _base_record(trade_id="C1", entry=102)       # copy of C2
        r_copy2 = _base_record(trade_id="C1", entry=102)       # copy of C1
        r_conf1 = _base_record(trade_id="K", entry=103)        # conflict version A
        r_conf2 = _base_record(trade_id="K", entry=999)        # conflict version B
        r_bad = _base_record(entry="not_a_number")             # validation error
        result = convert_trades([r_good1, r_good2, r_copy1, r_copy2, r_conf1, r_conf2, r_bad])
        assert result.total_records == 7
        assert result.accepted == 3   # G1, G2, C1(first copy)
        assert result.deduplicated == 1  # C1(second copy)
        assert result.rejected == 3  # K(conflict A), K(conflict B), bad
        assert result.accepted + result.rejected + result.deduplicated == 7

    # --- Warnings/errors preserve original indices ---
    def test_warnings_preserve_indices(self):
        r1 = _base_record(trade_id="D", entry=100)
        r2 = _base_record(trade_id="D", entry=100)
        r3 = _base_record(trade_id="D", entry=100)
        result = convert_trades([r1, r2, r3])
        assert result.warnings[0].record_index == 1
        assert result.warnings[1].record_index == 2

    def test_errors_preserve_indices(self):
        r1 = _base_record(trade_id="X", entry=100)
        r2 = _base_record(trade_id="X", entry=200)
        result = convert_trades([r1, r2])
        indices = sorted(e.record_index for e in result.errors)
        assert indices == [0, 1]

    # --- Reordered batches compare identities, fingerprints, categories, counts ---
    def test_reordered_batches_consistent(self):
        r1 = _base_record(trade_id="A", entry=100)
        r2 = _base_record(trade_id="A", entry=100)
        r3 = _base_record(trade_id="B", entry=200)
        res_ab = convert_trades([r1, r2, r3])
        res_ba = convert_trades([r3, r2, r1])
        assert res_ab.accepted == res_ba.accepted
        assert res_ab.rejected == res_ba.rejected
        assert res_ab.deduplicated == res_ba.deduplicated
        ids_ab = sorted(t.trade_id for t in res_ab.trades)
        ids_ba = sorted(t.trade_id for t in res_ba.trades)
        assert ids_ab == ids_ba

# ===========================================================================

class TestFarsCompatibility:
    def test_trade_has_all_fars_fields(self):
        rec = _base_record(
            t="2024-06-01T10:00:00+00:00",
            strategy="test_strat",
            side=1,
            entry=100,
            stop=95,
            exit_price=110,
            pnl_net=10,
        )
        result = convert_trades([rec], config=ConversionConfig(symbol="MNQ"))
        trade = result.trades[0]
        assert trade.r_result == pytest.approx(2.0)
        assert trade.trade_id != ""
        assert trade.timestamp is not None
        assert trade.date == "2024-06-01"
        assert trade.asset == "MNQ"
        assert trade.direction == "long"
        assert trade.entry_price == 100.0
        assert trade.stop_price == 95.0
        assert trade.exit_price == 110.0
        assert trade.strategy == "test_strat"
        assert isinstance(trade.metadata, dict)

    def test_result_structure(self):
        rec = _base_record()
        result = convert_trades([rec])
        assert isinstance(result, ConversionResult)
        assert isinstance(result.trades, tuple)
        assert isinstance(result.errors, tuple)
        assert isinstance(result.warnings, tuple)
        assert result.total_records == 1
        assert result.accepted + result.rejected == result.total_records

    def test_empty_input(self):
        result = convert_trades([])
        assert result.accepted == 0
        assert result.rejected == 0
        assert result.total_records == 0

    def test_multiple_trades_batch(self):
        records = [
            _base_record(
                t=f"2024-06-01T{10+i}:00:00+00:00",
                entry=100 + i,
                stop=95 + i,
                pnl_net=5 + i,
                trade_id=f"T-{i}",
            )
            for i in range(5)
        ]
        result = convert_trades(records, config=ConversionConfig(symbol="MNQ"))
        assert result.accepted == 5
        assert all(t.asset == "MNQ" for t in result.trades)

    def test_result_rejects_none_when_errors(self):
        rec = _base_record(side=0)
        result = convert_trades([rec])
        assert result.has_errors
        assert result.rejected == 1

    def test_monetary_zero_dollar_risk(self):
        """risk * qty * dpp = 0 when entry == stop but that's already caught."""
        rec = _base_record(entry=100, stop=100, pnl_net=10)
        cfg = ConversionConfig(pnl_units="monetary", quantity=1, dollar_per_point=1.0)
        result = convert_trades([rec], config=cfg)
        assert result.rejected == 1
        assert result.errors[0].code == "ZERO_OR_NON_FINITE_RISK"

    def test_strict_symbol_no_invention(self):
        """Adapter never invents symbol specs for unknown contracts."""
        rec = _base_record(asset="UNKNOWN_FUTURES_CONTRACT")
        result = convert_trades([rec])
        assert result.rejected == 1
        assert result.errors[0].code == "INVALID_SYMBOL"





# ===========================================================================
# 10. Round-trip con ingestiÃ³n real de FARS
# ===========================================================================

import os as _os
from pathlib import Path as _Path

_RT_DIR = _Path(_os.environ.get("FARS_TEST_TMP", str(_Path(__file__).parent.parent / "_test_tmp_rt")))


class TestRoundTripIngestion:
    """Verify adapter-produced trades survive a CSV round-trip through
    src.ingestion.load_trade_csv(), satisfying A1 acceptance criterion
    'round-trip con ingestiÃ³n real'."""

    @staticmethod
    def _write_trades_csv(directory, trades, filename="trades.csv"):
        """Write Trade objects to a canonical CSV compatible with load_trade_csv."""
        import csv as csv_mod
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        headers = [
            "r_result", "trade_id", "timestamp", "asset",
            "direction", "entry_price", "stop_price",
            "exit_price", "strategy",
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv_mod.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for t in trades:
                writer.writerow({
                    "r_result": t.r_result,
                    "trade_id": t.trade_id,
                    "timestamp": t.timestamp.isoformat() if t.timestamp else "",
                    "asset": t.asset,
                    "direction": t.direction or "",
                    "entry_price": t.entry_price if t.entry_price is not None else "",
                    "stop_price": t.stop_price if t.stop_price is not None else "",
                    "exit_price": t.exit_price if t.exit_price is not None else "",
                    "strategy": t.strategy or "",
                })
        return path

    def test_roundtrip_fields_preserved(self):
        """Adapter trades survive CSV round-trip with all key fields intact."""
        from src.ingestion import load_trade_csv
        d = _RT_DIR / "fields"
        d.mkdir(parents=True, exist_ok=True)

        records = [
            _base_record(
                t="2024-06-01T10:00:00+00:00",
                side=1, entry=100, stop=95, exit_price=110,
                pnl_net=10, trade_id="RT-1",
            ),
            _base_record(
                t="2024-06-01T11:00:00+00:00",
                side=-1, entry=110, stop=115, exit_price=104,
                pnl_net=6, trade_id="RT-2",
            ),
        ]
        result = convert_trades(records, config=ConversionConfig(symbol="MNQ"))
        assert result.accepted == 2

        csv_path = self._write_trades_csv(d, result.trades)
        dataset = load_trade_csv(csv_path, outcomes_finalized=True)

        assert len(dataset.trades) == 2
        rt1 = dataset.trades[0]
        rt2 = dataset.trades[1]

        assert rt1.trade_id == result.trades[0].trade_id  # v2: deterministic hash
        assert rt1.direction == "long"
        assert rt1.entry_price == pytest.approx(100.0)
        assert rt1.stop_price == pytest.approx(95.0)
        assert rt1.exit_price == pytest.approx(110.0)
        assert rt1.r_result == pytest.approx(2.0)

        assert rt2.trade_id == result.trades[1].trade_id  # v2: deterministic hash
        assert rt2.direction == "short"
        assert rt2.entry_price == pytest.approx(110.0)
        assert rt2.r_result == pytest.approx(1.2)

    def test_roundtrip_audit_clean(self):
        """No audit errors on a well-formed adapter round-trip."""
        from src.ingestion import load_trade_csv
        d = _RT_DIR / "audit"
        d.mkdir(parents=True, exist_ok=True)

        records = [
            _base_record(trade_id=f"CL-{i}", pnl_net=3 + i)
            for i in range(10)
        ]
        result = convert_trades(records)
        assert result.accepted == 10

        csv_path = self._write_trades_csv(d, result.trades)
        dataset = load_trade_csv(csv_path, outcomes_finalized=True)

        assert dataset.audit.rejected_rows == 0
        assert not dataset.audit.has_errors

    def test_roundtrip_core_capability_available(self):
        """Core metrics capability is available after adapter round-trip."""
        from src.ingestion import load_trade_csv
        d = _RT_DIR / "cap"
        d.mkdir(parents=True, exist_ok=True)

        records = [_base_record(trade_id=f"CAP-{i}") for i in range(5)]
        result = convert_trades(records, config=ConversionConfig(symbol="MNQ"))  # match _base_record asset
        csv_path = self._write_trades_csv(d, result.trades)
        dataset = load_trade_csv(csv_path, outcomes_finalized=True)

        assert dataset.capabilities["core_metrics"].available

    def test_roundtrip_monetary_pnl_preserved(self):
        """Monetary PnL round-trips with correct R via config params."""
        from src.ingestion import load_trade_csv
        d = _RT_DIR / "monetary"
        d.mkdir(parents=True, exist_ok=True)

        rec = _base_record(
            pnl_net=200, entry=100, stop=95, trade_id="MON-RT",
        )
        cfg = ConversionConfig(
            pnl_units="monetary", quantity=2, dollar_per_point=2.0,
        )
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        original_r = result.trades[0].r_result

        csv_path = self._write_trades_csv(d, result.trades)
        dataset = load_trade_csv(csv_path, outcomes_finalized=True)

        assert len(dataset.trades) == 1
        assert dataset.trades[0].r_result == pytest.approx(original_r)


# ===========================================================================
# 11. IntegraciÃ³n con bootstrap (semilla fija)
# ===========================================================================

class TestBootstrapIntegration:
    """Verify adapter -> CSV -> load_trade_csv -> analyze_bootstrap round-trip
    with a fixed seed, satisfying A1 criterion on bootstrap eligibility."""

    SEED = 42
    N_TRADES = 60

    @staticmethod
    def _build_dataset():
        """Create a CanonicalTradeDataset from adapter output."""
        import csv as csv_mod
        from src.ingestion import load_trade_csv
        d = _RT_DIR / "bootstrap"
        d.mkdir(parents=True, exist_ok=True)

        records = []
        import random
        rng = random.Random(123)
        for i in range(60):
            side = 1 if rng.random() > 0.4 else -1
            entry = 100 + rng.uniform(-5, 5)
            risk = rng.uniform(2, 8)
            stop = entry - risk if side == 1 else entry + risk
            pnl = risk * rng.uniform(-1.5, 3)
            records.append({
                "t": f"2024-01-01T{8 + i // 8:02d}:{i % 8 * 7:02d}:00+00:00",
                "strategy": "bootstrap_test",
                "side": side,
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "exit_price": round(entry + pnl * 0.5, 2),
                "pnl_net": round(pnl, 2),
                "trade_id": f"BTS-{i:03d}",
                "outcome": "closed",
            })

        result = convert_trades(records, config=ConversionConfig(symbol="MNQ"))
        assert result.accepted == 60

        headers = [
            "r_result", "trade_id", "timestamp", "asset",
            "direction", "entry_price", "stop_price",
            "exit_price", "strategy",
        ]
        csv_path = d / "bootstrap_trades.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv_mod.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for t in result.trades:
                writer.writerow({
                    "r_result": t.r_result,
                    "trade_id": t.trade_id,
                    "timestamp": t.timestamp.isoformat() if t.timestamp else "",
                    "asset": t.asset,
                    "direction": t.direction or "",
                    "entry_price": t.entry_price if t.entry_price is not None else "",
                    "stop_price": t.stop_price if t.stop_price is not None else "",
                    "exit_price": t.exit_price if t.exit_price is not None else "",
                    "strategy": t.strategy or "",
                })

        return load_trade_csv(csv_path, outcomes_finalized=True)

    def test_bootstrap_runs_with_fixed_seed(self):
        """analyze_bootstrap produces deterministic output from adapter data."""
        from src.bootstrap import analyze_bootstrap, RESULT_SCHEMA_VERSION

        dataset = self._build_dataset()
        r1 = analyze_bootstrap(dataset, master_seed=self.SEED)
        r2 = analyze_bootstrap(dataset, master_seed=self.SEED)

        assert r1["schema_version"] == RESULT_SCHEMA_VERSION
        assert r1["rng"]["master_entropy"] == self.SEED
        assert r1 == r2

    def test_bootstrap_eligibility_structure(self):
        """Bootstrap eligibility returns a valid state with reasons."""
        from src.bootstrap import analyze_bootstrap

        dataset = self._build_dataset()
        result = analyze_bootstrap(dataset, master_seed=self.SEED)

        elig = result["eligibility"]
        assert "state" in elig
        assert "reasons" in elig
        valid_states = {"iid_eligible", "dependent_resampling_candidate", "unsupported_or_inconclusive"}
        assert elig["state"] in valid_states

    def test_bootstrap_estimands_present(self):
        """Estimands structure is correct after adapter round-trip."""
        from src.bootstrap import analyze_bootstrap

        dataset = self._build_dataset()
        result = analyze_bootstrap(dataset, master_seed=self.SEED)

        for key in ("expectancy", "win_rate", "std"):
            est = result["estimands"][key]
            assert "status" in est
            assert "value" in est
            assert "intervals" in est

    def test_bootstrap_provenance_from_adapter(self):
        """Provenance in bootstrap result references the adapter-sourced dataset."""
        from src.bootstrap import analyze_bootstrap

        dataset = self._build_dataset()
        result = analyze_bootstrap(dataset, master_seed=self.SEED)

        prov = result["provenance"]
        assert prov["schema_version"] == "1.2-phase8a"
        assert "source_sha256" in prov
        assert len(prov["source_sha256"]) == 64

# Regression tests for P1-B1 review items

# ===========================================================================
# 12. P1-B1 review: focused regression tests for pending items
# ===========================================================================


class TestReviewP1B1Regression:

    def test_deduplicated_field_at_end_and_default(self):
        rec = _base_record()
        result = convert_trades([rec])
        fields = list(result.__dataclass_fields__.keys())
        assert fields.index("deduplicated") > fields.index("rejected")
        assert result.deduplicated == 0

    def test_deduplicated_invariant_holds(self):
        recs = [
            _base_record(trade_id="X"),
            _base_record(trade_id="X"),
            _base_record(trade_id="Y", entry=200),
            _base_record(trade_id="Z", entry=300, pnl_net=-999),
        ]
        result = convert_trades(recs)
        assert result.accepted + result.rejected + result.deduplicated == result.total_records

    def test_symbol_used_from_accepted_trades(self):
        recs = [_base_record(asset="MNQ") for _ in range(3)]
        r = convert_trades(recs, config=ConversionConfig(symbol="MNQ"))
        assert r.symbol_used == "MNQ"

    def test_symbol_used_none_for_mixed_batch(self):
        recs = [
            _base_record(asset="MNQ", entry=100),
            _base_record(asset="MES", entry=200),
        ]
        r = convert_trades(recs)
        assert r.symbol_used is None

    def test_config_symbol_rejects_unknown_symbol(self):
        rec = _base_record()
        cfg = ConversionConfig(symbol="XYZABC")
        result = convert_trades([rec], config=cfg)
        invalid_sym_errors = [e for e in result.errors if e.code == "INVALID_SYMBOL"]
        assert any(e.code == "INVALID_CONFIG_SYMBOL" for e in result.errors)
        assert result.accepted == 0

    def test_config_symbol_validates_record_asset_against_config(self):
        rec = _base_record(asset="MNQ")
        cfg = ConversionConfig(symbol="MES")
        result = convert_trades([rec], config=cfg)
        assert any(e.code == "SYMBOL_CONFLICT" for e in result.errors)

    def test_pnl_net_boolean_true_rejected(self):
        rec = _base_record(pnl_net=True, entry=100, stop=95)
        result = convert_trades([rec])
        assert result.accepted == 0
        assert any(e.code == "INVALID_PNL_NET_TYPE" for e in result.errors)

    def test_pnl_net_boolean_false_rejected(self):
        rec = _base_record(pnl_net=False, entry=100, stop=95)
        result = convert_trades([rec])
        assert result.accepted == 0
        assert any(e.code == "INVALID_PNL_NET_TYPE" for e in result.errors)

    def test_side_decimal_non_integer_rejected(self):
        from decimal import Decimal
        rec = _base_record(side=Decimal("1.5"))
        result = convert_trades([rec])
        assert result.accepted == 0
        assert any(e.code == "SIDE_NOT_INTEGER" for e in result.errors)

    def test_side_decimal_integer_accepted(self):
        from decimal import Decimal
        rec = _base_record(side=Decimal("1"))
        result = convert_trades([rec])
        assert result.accepted == 1
        assert result.trades[0].direction == "long"

    def test_source_dataset_whitespace_only_rejected(self):
        cfg = ConversionConfig(outcomes_finalized=True, source_dataset="   ")
        rec = _base_record()
        result = convert_trades([rec], config=cfg)
        assert any(e.code == "MISSING_SOURCE_DATASET" for e in result.errors)

    def test_source_dataset_valid_preserved_in_metadata(self):
        cfg = ConversionConfig(outcomes_finalized=True, source_dataset="ds-2024Q1")
        cfg = ConversionConfig(outcomes_finalized=True, source_dataset="ds-2024Q1")
        rec = _base_record()
        rec.pop("outcome", None)  # Remove outcome to trigger source_dataset path
        result = convert_trades([rec], config=cfg)
        assert result.accepted == 1
        m = result.trades[0].metadata
        assert m.get("source_dataset") == "ds-2024Q1"
        assert m.get("outcomes_finalized") is True
    def test_invalid_source_timezone_rejected(self):
        cfg = ConversionConfig(source_timezone="Not/A/Zone")
        rec = _base_record(t="2024-06-01T10:00:00")
        with pytest.raises(ValueError, match="valid IANA zone"):
            convert_trades([rec], config=cfg)

    def test_invalid_analysis_timezone_rejected(self):
        cfg = ConversionConfig(
            source_timezone="UTC",
            analysis_timezone="Bogus/Zone",
        )
        rec = _base_record(t="2024-06-01T10:00:00")
        with pytest.raises(ValueError, match="valid IANA zone"):
            convert_trades([rec], config=cfg)

    def test_fold_negative_rejected_for_ambiguous(self):
        cfg = ConversionConfig(
            source_timezone="America/New_York",
            ambiguous_timestamp_fold=-1,
        )
        rec = _base_record(t="2024-11-03T01:30:00")
        result = convert_trades([rec], config=cfg)
        assert any(e.code == "AMBIGUOUS_TIMESTAMP" for e in result.errors)

    def test_timestamp_comma_rejected(self):
        rec = _base_record(t="2024-06-01T10:00:00,000+00:00")
        result = convert_trades([rec])
        assert result.accepted == 0
        assert any(e.code == "INVALID_TIMESTAMP_FORMAT" for e in result.errors)

    def test_timestamp_seven_decimal_digits_rejected(self):
        rec = _base_record(t="2024-06-01T10:00:00.1234567+00:00")
        result = convert_trades([rec])
        assert any(e.code == "UNSUPPORTED_TIMESTAMP_PRECISION" for e in result.errors)

        rec = _base_record(t="2024-06-01T10:00:00.1234567+00:00")
        result = convert_trades([rec])
        assert any(e.code == "UNSUPPORTED_TIMESTAMP_PRECISION" for e in result.errors)

    def test_timestamp_six_decimal_digits_accepted(self):
        rec = _base_record(t="2024-06-01T10:00:00.123456+00:00")
        result = convert_trades([rec])
        assert result.accepted == 1

    def test_roundtrip_v2_identity_deterministic(self):
        recs = [
            _base_record(trade_id="DET-1", entry=100),
            _base_record(trade_id="DET-2", entry=200),
        ]
        r1 = convert_trades(recs, config=ConversionConfig(symbol="MNQ"))
        r2 = convert_trades(recs, config=ConversionConfig(symbol="MNQ"))
        assert r1.trades[0].trade_id == r2.trades[0].trade_id
        assert r1.trades[1].trade_id == r2.trades[1].trade_id
        assert r1.trades[0].trade_id != r1.trades[1].trade_id

    def test_roundtrip_provenance_preserved(self):
        from src.ingestion import load_trade_csv
        d = _RT_DIR / "prov"
        d.mkdir(parents=True, exist_ok=True)
        rec = _base_record(trade_id="PROV-1")
        result = convert_trades([rec], config=ConversionConfig(symbol="MNQ"))
        assert result.accepted == 1
        csv_path = TestRoundTripIngestion._write_trades_csv(d, result.trades)
        dataset = load_trade_csv(csv_path, outcomes_finalized=True)
        assert len(dataset.trades) == 1
        assert dataset.trades[0].trade_id == result.trades[0].trade_id
