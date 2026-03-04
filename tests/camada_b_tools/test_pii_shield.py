from pii_shield import PIIMaskingContext, _category_to_label


def test_category_labels():
    assert _category_to_label("Person") == "NOME"
    assert _category_to_label("PTTaxIdentificationNumber") == "NIF"
    assert _category_to_label("InternationalBankingAccountNumber") == "IBAN"


def test_masking_context_unmask():
    ctx = PIIMaskingContext()
    p1 = ctx.add_mapping("Person", "Joao Silva")
    p2 = ctx.add_mapping("PTTaxIdentificationNumber", "123456789")

    assert p1 == "[NOME_1]"
    assert p2 == "[NIF_1]"

    text = "O cliente [NOME_1] com NIF [NIF_1] reclamou."
    result = ctx.unmask(text)
    assert result == "O cliente Joao Silva com NIF 123456789 reclamou."


def test_multiple_same_category():
    ctx = PIIMaskingContext()
    p1 = ctx.add_mapping("Person", "Joao")
    p2 = ctx.add_mapping("Person", "Maria")
    assert p1 == "[NOME_1]"
    assert p2 == "[NOME_2]"


def test_unmask_nested_tool_arguments():
    ctx = PIIMaskingContext()
    ctx.mappings = {"[NOME_1]": "Joao", "[NIF_1]": "123456789"}
    nested = {
        "nome": "[NOME_1]",
        "meta": {"nif": "[NIF_1]"},
        "lista": ["[NOME_1]", 1, {"v": "[NIF_1]"}],
    }
    assert ctx.unmask_any(nested) == {
        "nome": "Joao",
        "meta": {"nif": "123456789"},
        "lista": ["Joao", 1, {"v": "123456789"}],
    }

