# Source adapters

## Authoritative profile sources

- ICTV current taxonomy and report pages: https://ictv.global/
- ViralZone: https://viralzone.expasy.org/
- NCBI E-utilities/taxonomy as a supplemental authority path.

## Scholarly discovery

- PubMed E-utilities
- Europe PMC REST API
- Crossref REST API using created-date and online-publication-date windows
- Semantic Scholar Academic Graph API
- OpenAlex Works API
- bioRxiv and medRxiv API

## Abstract and full-text recovery

- PubMed XML abstracts
- Europe PMC core metadata and fullTextXML
- NCBI PMC BioC
- Crossref full-text/TDM links
- Unpaywall open-access locations using CROSSREF_MAILTO as the contact email
- Semantic Scholar/OpenAlex open PDF links
- DOI/publisher landing-page metadata and public HTML
- Public PDFs parsed temporarily with PyMuPDF

## News discovery

- Google News RSS in English and Simplified Chinese
- Bing News RSS
- GDELT DOC 2.0
- ReliefWeb API
- WHO website search

Discovery aggregators are not treated as authorities. The final card preserves the publisher, source URL, content acquisition status, and model audit.
