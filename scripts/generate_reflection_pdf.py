from pathlib import Path
try:
    from weasyprint import HTML
    md_path = Path('generated/SHIMS_Complete_Reflection_and_Evolution_Roadmap.md')
    import markdown
    html_content = markdown.markdown(md_path.read_text(encoding='utf-8'), extensions=['tables'])
    full_html = '<html><head><meta charset="utf-8"></head><body>' + html_content + '</body></html>'
    HTML(string=full_html).write_pdf('generated/SHIMS_Complete_Reflection_and_Evolution_Roadmap.pdf')
    print('PDF generated: generated/SHIMS_Complete_Reflection_and_Evolution_Roadmap.pdf')
except Exception as e:
    print('Error:', e)
