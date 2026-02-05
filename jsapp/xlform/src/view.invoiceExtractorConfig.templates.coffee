module.exports = do ->

  invoiceExtractorConfigInput = (repeatGroup, fieldMappings) ->
    # Generate rows for existing field mappings
    mappingRows = ''
    if fieldMappings and fieldMappings.length > 0
      for mapping, index in fieldMappings
        mappingRows += """
        <div class='invoice-field-mapping' data-index='#{index}'>
          <input type='text' class='text field-from' placeholder='External field name' value='#{mapping.from}' />
          <span class='mapping-arrow'>→</span>
          <input type='text' class='text field-to' placeholder='Form field name' value='#{mapping.to}' />
          <button type='button' class='btn-remove-mapping'>×</button>
        </div>
        """
    else
      # Default empty row
      mappingRows = """
      <div class='invoice-field-mapping' data-index='0'>
        <input type='text' class='text field-from' placeholder='External field name' value='' />
        <span class='mapping-arrow'>→</span>
        <input type='text' class='text field-to' placeholder='Form field name' value='' />
        <button type='button' class='btn-remove-mapping'>×</button>
      </div>
      """

    template = """
    <div class='card__settings__fields__field invoice-extractor-config-container'>
      <label>#{t("Invoice Repeat Group")}</label>
      <span class='settings__input'>
        <input class='text invoice-repeat-group' type='text' value='#{repeatGroup or ''}' placeholder='#{t("e.g. invoices")}' />
      </span>
      <div class='help-text'>#{t("Name of the repeat group containing invoice items")}</div>
    </div>
    <div class='card__settings__fields__field invoice-extractor-config-container'>
      <label>#{t("Field Mappings")}</label>
      <div class='help-text'>#{t("Map fields from external service to form fields")}</div>
      <div class='invoice-field-mappings'>
        #{mappingRows}
      </div>
      <button type='button' class='btn-add-mapping'>+ #{t("Add Field Mapping")}</button>
    </div>
    """
    
    return template

  return invoiceExtractorConfigInput: invoiceExtractorConfigInput


