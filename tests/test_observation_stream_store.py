from correlis_store import ObservationRepository


def test_scoped_scanner_api_is_available():
    assert hasattr(ObservationRepository, "scan_scoped_sequence_page")
