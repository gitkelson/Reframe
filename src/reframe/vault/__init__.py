"""Attachment vault (R-160 - R-169): content-addressed copies of attached files.

Backend chosen by the §10.3 gate (see ``tools/vault_bench/RESULTS.md``): whole-file zlib blobs named by the
SHA-256 of the original content. No external executable, no password.
"""
