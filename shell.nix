{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [
    # Python dev managed by uv (uv provisions the interpreter itself)
    pkgs.uv

    # VCS: jj colocated with git
    pkgs.git
    pkgs.jujutsu

    # Spec-driven development
    pkgs.openspec
    pkgs.pandoc        # bin/openspec-preview renders change artifacts to HTML

    # TLA+ model checking (tla-* skills)
    pkgs.tlaplus

    pkgs.cacert
  ];

  shellHook = ''
    export SSL_CERT_FILE="${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
    export UV_PYTHON_PREFERENCE=managed
  '';
}
