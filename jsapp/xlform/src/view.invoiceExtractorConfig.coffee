Backbone = require 'backbone'
$baseView = require './view.pluggedIn.backboneView'
$viewTemplates = require './view.templates'

module.exports = do ->
  class InvoiceExtractorConfigView extends $baseView
    className: 'invoice-extractor-config card__settings__fields__field'
    events: {
      'input .invoice-repeat-group': 'onRepeatGroupChange'
      'input .field-from': 'onMappingChange'
      'input .field-to': 'onMappingChange'
      'click .btn-add-mapping': 'addMapping'
      'click .btn-remove-mapping': 'removeMapping'
    }

    initialize: ({@rowView, @configValue=''}) -> 
      @parseConfig()
      return

    parseConfig: ->
      # Parse the JSON config or use defaults
      if @configValue and @configValue isnt ''
        try
          config = JSON.parse(@configValue)
          @repeatGroup = config.repeatGroup or ''
          @fieldMappings = []
          if config.fieldMapping
            for from, to of config.fieldMapping
              @fieldMappings.push({from: from, to: to})
        catch e
          console.error('Error parsing invoice extractor config:', e)
          @repeatGroup = ''
          @fieldMappings = []
      else
        @repeatGroup = ''
        @fieldMappings = []

    render: ->
      template = $($viewTemplates.$$render("InvoiceExtractorConfigView.input", @repeatGroup, @fieldMappings))
      @$el.html(template)
      return @

    insertInDOM: (rowView)->
      @$el.appendTo(rowView.defaultRowDetailParent)
      return

    onRepeatGroupChange: (evt) ->
      @repeatGroup = evt.currentTarget.value
      @saveConfig()
      return

    onMappingChange: (evt) ->
      @saveConfig()
      return

    addMapping: (evt) ->
      evt.preventDefault()
      # Find the last index
      lastIndex = @$('.invoice-field-mapping').length
      newRow = """
      <div class='invoice-field-mapping' data-index='#{lastIndex}'>
        <input type='text' class='text field-from' placeholder='External field name' value='' />
        <span class='mapping-arrow'>→</span>
        <input type='text' class='text field-to' placeholder='Form field name' value='' />
        <button type='button' class='btn-remove-mapping'>×</button>
      </div>
      """
      @$('.invoice-field-mappings').append(newRow)
      return

    removeMapping: (evt) ->
      evt.preventDefault()
      $(evt.currentTarget).closest('.invoice-field-mapping').remove()
      @saveConfig()
      return

    saveConfig: ->
      # Collect all field mappings
      fieldMapping = {}
      @$('.invoice-field-mapping').each (index, element) =>
        $el = $(element)
        fromField = $el.find('.field-from').val().trim()
        toField = $el.find('.field-to').val().trim()
        if fromField and toField
          fieldMapping[fromField] = toField

      # Build the config object
      config = {
        repeatGroup: @repeatGroup
        fieldMapping: fieldMapping
      }

      # Convert to JSON string
      @configValue = JSON.stringify(config)
      @rowView.model.setInvoiceExtractorConfig(@configValue)
      @rowView.model.getSurvey().trigger('change')
      return

  return InvoiceExtractorConfigView: InvoiceExtractorConfigView


