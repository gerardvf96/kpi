Backbone = require 'backbone'
$baseView = require './view.pluggedIn.backboneView'
$viewTemplates = require './view.templates'

module.exports = do ->
  class InvoiceExtractorConfigView extends $baseView
    className: 'invoice-extractor-config card__settings__fields__field'
    events: {
      'input input': 'onChange'
    }
    placeholder: t("e.g. \"config-name\"")

    initialize: ({@rowView, @configValue=''}) -> return

    render: ->
      template = $($viewTemplates.$$render("InvoiceExtractorConfigView.input", @configValue, @placeholder))
      @$el.html(template)
      return @

    insertInDOM: (rowView)->
      @$el.appendTo(rowView.defaultRowDetailParent)
      return

    onChange: (evt) ->
      @configValue = evt.currentTarget.value
      @rowView.model.setInvoiceExtractorConfig(@configValue)
      @rowView.model.getSurvey().trigger('change')
      return

  return InvoiceExtractorConfigView: InvoiceExtractorConfigView

