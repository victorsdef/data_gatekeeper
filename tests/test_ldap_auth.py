import auth.ldap_auth as ldap_auth


def test_parse_ldapsearch_output_collects_repeated_attributes():
    output = """
dn: CN=Jose Perez,OU=Users,DC=austro,DC=grpfin
cn: Jose Perez
mail: jose.perez@austro.grpfin
memberOf: CN=Gatekeeper_Admin,OU=Groups,DC=austro,DC=grpfin
memberOf: CN=OtroGrupo,OU=Groups,DC=austro,DC=grpfin
"""

    parsed = ldap_auth._parse_ldapsearch_output(output)

    assert parsed["cn"] == ["Jose Perez"]
    assert parsed["mail"] == ["jose.perez@austro.grpfin"]
    assert len(parsed["memberOf"]) == 2


def test_ldapsearch_authenticate_returns_user_info(monkeypatch):
    monkeypatch.setattr(ldap_auth, "LDAP_SERVER", "ldap://austro.grpfin:389")
    monkeypatch.setattr(ldap_auth, "LDAP_BASE_DN", "DC=austro,DC=grpfin")
    monkeypatch.setattr(ldap_auth, "LDAP_DOMAIN", "austro.grpfin")
    monkeypatch.setattr(ldap_auth, "LDAP_REQUIRED_GROUP", "")
    monkeypatch.setattr(ldap_auth, "LDAP_SEARCH_ATTRIBUTE", "sAMAccountName")
    monkeypatch.setattr(ldap_auth.shutil, "which", lambda _: "/usr/bin/ldapsearch")

    class Completed:
        returncode = 0
        stdout = """
dn: CN=Jose Berrezueta,OU=Users,DC=austro,DC=grpfin
cn: Jose Berrezueta
mail: jose.berrezueta@austro.grpfin
memberOf: CN=Gatekeeper_Admin,OU=Groups,DC=austro,DC=grpfin
"""
        stderr = ""

    def fake_run(command, capture_output, text, check):
        assert command[0] == "/usr/bin/ldapsearch"
        assert "-D" in command
        assert "jberrezueta@austro.grpfin" in command
        return Completed()

    monkeypatch.setattr(ldap_auth.subprocess, "run", fake_run)

    result = ldap_auth._ldapsearch_authenticate("jberrezueta", "secret")

    assert result == {
        "username": "jberrezueta",
        "nombre": "Jose Berrezueta",
        "email": "jose.berrezueta@austro.grpfin",
        "rol": "Admin",
    }
