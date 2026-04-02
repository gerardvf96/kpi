_ = require 'underscore'
Backbone = require 'backbone'
$modelUtils = require './model.utils'
$configs = require './model.configs'
$viewUtils = require './view.utils'
$icons = require './view.icons'
$hxl = require './view.rowDetail.hxlDict'

$viewRowDetailSkipLogic = require './view.rowDetail.SkipLogic'
$viewTemplates = require './view.templates'

module.exports = do ->
  viewRowDetail = {}

  class viewRowDetail.DetailView extends Backbone.View
    ###
    The DetailView class is a base class for details
    of each row of the XLForm. When the view is initialized,
    a mixin from "DetailViewMixins" is applied.
    ###
    className: "card__settings__fields__field  dt-view dt-view--depr"
    initialize: ({@rowView})->
      unless @model.key
        throw new Error "RowDetail does not have key"
      @extraClass = "xlf-dv-#{@model.key}"
      _.extend(@, viewRowDetail.DetailViewMixins[@model.key] || viewRowDetail.DetailViewMixins.default)
      @$el.addClass(@extraClass)

      return

    render: ()->
      rendered = @html()
      if rendered
        @$el.html rendered

      @afterRender && @afterRender()
      return @

    html: ()->
      $viewTemplates.$$render('xlfDetailView', @)

    listenForCheckboxChange: (opts={})->
      el = opts.el || @$('input[type=checkbox]').get(0)
      $el = $(el)
      changing = false
      _requiredBox = @model.key is "required"

      reflectValueInEl = ()=>
        if !changing
          val = @model.get('value')
          if val is true or val in $configs.truthyValues
            $el.prop('checked', true)
      @model.on 'change:value', reflectValueInEl
      reflectValueInEl()
      $el.on 'change', ()=>
        changing = true
        @model.set('value', $el.prop('checked'))
        if _requiredBox
          $el.parents('.card').eq(0).toggleClass('card--required', $el.prop('checked'))
        changing = false
      return

    listenForInputChange: (opts={})->
      # listens to checkboxes and input fields and ensures
      # the model's value is reflected in the element and changes
      # to the element are reflected in the model (with transformFn
      # applied)
      el = opts.el || @$('input').get(0)

      $el = $(el)
      transformFn = opts.transformFn || false
      inputType = opts.inputType
      inTransition = false

      changeModelValue = ($elVal)=>
        # preventing race condition
        if !inTransition
          inTransition = true
          @model.set('value', $elVal)
          reflectValueInEl(true)
          inTransition = false

      reflectValueInEl = (force=false)=>
        # This should never change the model value
        if force || !inTransition
          modelVal = @model.get('value')
          if inputType is 'checkbox'
            if !_.isBoolean(modelVal)
              modelVal = modelVal in $configs.truthyValues
            # triggers element change event
            $el.prop('checked', modelVal)
          else
            # triggers element change event
            $el.val(modelVal)

      reflectValueInEl()
      @model.on 'change:value', reflectValueInEl

      $el.on 'change', ()=>
        $elVal = $el.val()
        if transformFn
          $elVal = transformFn($elVal)
        changeModelValue($elVal)

      $el.on('keyup', (evt) =>
        if evt.key is 'Enter' or evt.keyCode is 13
          $el.blur()
      )
      return

    _insertInDOM: (where, how) ->
      where[how || 'append'](@el)
    insertInDOM: (rowView)->
      @_insertInDOM rowView.defaultRowDetailParent

  viewRowDetail.Templates = {
    textbox: (cid, key, key_label = key, input_class = '') ->
      @field """<input type="text" name="#{key}" id="#{cid}" class="#{input_class}" dir="auto" />""", cid, key_label

    checkbox: (cid, key, key_label = key, input_label = t("Yes")) ->
      input_label = input_label
      @field """<input type="checkbox" name="#{key}" id="#{cid}"/> <label for="#{cid}">#{input_label}</label>""", cid, key_label

    dropdown: (cid, key, values, key_label = key) ->
      select = """<select id="#{cid}">"""

      for value in values
        if typeof(value) is 'string'
          select += """<option value="#{value}">#{value}</option>"""
        else
          # HACK FIX: we're expecting an array of this structure [['option', 'Description'], ...] in order
          # to display the option next to some helpful text in a dropdown
          select += """<option value="#{value[0]}">#{value[0]} (#{value[1]})</option>"""

      select += "</select>"

      @field select, cid, key_label

    hxlTags: (cid, key, key_label = key, value = '', hxlTag = '', hxlAttrs = '') ->
      tags = """<input type="text" name="#{key}" id="#{cid}" class="hxlValue hidden" value="#{value}"  />"""
      tags += """ <div class="settings__hxl"><input id="#{cid}-tag" class="hxlTag" value="#{hxlTag}" type="hidden" />"""
      tags += """ <input id="#{cid}-attrs" class="hxlAttrs" value="#{hxlAttrs}" type="hidden" /></div>"""

      @field tags, cid, key_label

    field: (input, cid, key_label) ->
      """
      <div class="card__settings__fields__field">
        <label for="#{cid}">#{key_label}:</label>
        <span class="settings__input">
          #{input}
        </span>
      </div>
      """
  }

  viewRowDetail.DetailViewMixins = {}

  viewRowDetail.DetailViewMixins.type =
    html: -> false
    insertInDOM: (rowView)->
      typeStr = @model.get("typeId")
      if !(@model._parent.constructor.kls is "Group")
        iconClassName = $icons.get(typeStr)?.get("iconClassName")
        if !iconClassName
          console?.error("could not find icon for type: #{typeStr}")
          iconClassName = "k-icon k-icon-alert"
        rowView.$el.find(".card__header-icon").addClass('k-icon').addClass(iconClassName)
      return


  viewRowDetail.DetailViewMixins.file =
    html: ->
      @fieldTab = "active"
      @$el.addClass("card__settings__fields--file")
      available_files = this.model.getSurvey().availableFiles || []
      file = available_files[0]
      if available_files.length is 0
        return viewRowDetail.Templates.textbox @cid, @model.key, label, 'text'
      else
        options = []
        for file in available_files
          options.push "<option>#{file.metadata.filename}</option>"
        uniq = "select-file-#{@cid}"
        tfile = t("Choices File")
        return """
            <label for="#{uniq}">#{tfile}:</label>
            <div class="settings__input">
              <select id="#{uniq}">
                #{options.join('')}
              </select>
            </div>
        """

    afterRender: ->
      @$el.find('select').eq(0).val(@model.get("value"))
      @listenForSelectChange(@$('select').eq(0))

    listenForSelectChange: ($select) ->
      $select.on 'change', (evt) =>
        targetval = evt.target.value
        @model.set('value', targetval)


  viewRowDetail.DetailViewMixins.label =
    html: -> false
    insertInDOM: (rowView)->
      cht = rowView.$label
      cht.value = @model.get('value')
      return @
    afterRender: ->
      @listenForInputChange({
        el: this.rowView.$label,
        transformFn: (value) ->
          value = value.replace(new RegExp(String.fromCharCode(160), 'g'), '')
          value = value.replace /\t/g, ' '
          return value
      })
      return

  viewRowDetail.DetailViewMixins.hint =
    html: -> false
    insertInDOM: (rowView) ->
      hintEl = rowView.$hint
      hintEl.value = @model.get("value")
      return @
    afterRender: ->
      @listenForInputChange({
        el: this.rowView.$hint
      })
      return

  viewRowDetail.DetailViewMixins.guidance_hint =
    html: ->
      @$el.addClass("card__settings__fields--active")
      viewRowDetail.Templates.textbox @cid, @model.key, t("Guidance hint"), 'text'
    afterRender: ->
      @listenForInputChange()

  viewRowDetail.DetailViewMixins.constraint_message =
    html: ->
      @$el.addClass("card__settings__fields--active")
      viewRowDetail.Templates.textbox @cid, @model.key, t("Error Message"), 'text'
    insertInDOM: (rowView)->
      @_insertInDOM rowView.cardSettingsWrap.find('.js-card-settings-validation-criteria').eq(0)
    afterRender: ->
      @listenForInputChange()

  # parameters are handled per case
  viewRowDetail.DetailViewMixins.parameters =
    html: -> false
    insertInDOM: (rowView)-> return

  # body::accept is handled in custom view
  viewRowDetail.DetailViewMixins['body::accept'] =
    html: -> false
    insertInDOM: (rowView)-> return

  # body::invoice-extractor-config is handled in custom view
  viewRowDetail.DetailViewMixins['body::invoice-extractor-config'] =
    html: -> false
    insertInDOM: (rowView)-> return

  viewRowDetail.DetailViewMixins.relevant =
    html: ->
      @$el.addClass("card__settings__fields--active")
      """
      <div class="card__settings__fields__field relevant__editor">
      </div>
      """

    afterRender: ->
      @$el.find(".relevant__editor").html("""
        <div class="skiplogic__main"></div>
        <p class="skiplogic__extras">
        </p>
      """)

      @target_element = @$('.skiplogic__main')

      @model.facade.render @target_element

    insertInDOM: (rowView) ->
      @_insertInDOM rowView.cardSettingsWrap.find('.js-card-settings-skip-logic').eq(0)

  viewRowDetail.DetailViewMixins.constraint =
    html: ->
      @$el.addClass("card__settings__fields--active")
      """
      <div class="card__settings__fields__field constraint__editor">
      </div>
      """
    afterRender: ->
      @$el.find(".constraint__editor").html("""
        <div class="skiplogic__main"></div>
        <p class="skiplogic__extras">
        </p>
      """)

      @target_element = @$('.skiplogic__main')

      @model.facade.render @target_element

    insertInDOM: (rowView) ->
      @_insertInDOM rowView.cardSettingsWrap.find('.js-card-settings-validation-criteria')

  viewRowDetail.DetailViewMixins.name =
    html: ->
      @fieldTab = "active"
      @$el.addClass("card__settings__fields--#{@fieldTab}")
      viewRowDetail.Templates.textbox @cid, @model.key, t("Data column name"), 'text'
    afterRender: ->
      @listenForInputChange(transformFn: (value)=>
        value_chars = value.split('')
        if !/[\w_]/.test(value_chars[0])
          value_chars.unshift('_')

        @model.set 'value', value
        @model.deduplicate @model.getSurvey()
      )
      update_view = () =>
        $inp = @$el.find('input').eq(0)
        modelval = @model.get("value")
        if modelval
          $inp.val modelval
        else
          parent_label = @model._parent.getValue('label')
          if parent_label instanceof Array
            console.error("Bad input? Why is this an array?", parent_label)
            parent_label = parent_label[0]
          sluggified_label = $modelUtils.sluggifyLabel(parent_label)
          $inp.val sluggified_label
      update_view()

      @model._parent.get('label').on 'change:value', update_view
  # insertInDom: (rowView)->
    #   # default behavior...
    #   rowView.defaultRowDetailParent.append(@el)

  viewRowDetail.DetailViewMixins.tags =
    html: ->
      @fieldTab = "active"
      @$el.addClass("card__settings__fields--#{@fieldTab}")
      label = t("HXL")
      if (@model.get("value"))
        tags = @model.get("value")
        hxlTag = ''
        hxlAttrs = []
        hxlAttrsString = ''

        if _.isArray(tags)
          _.map(tags, (_t, i)->
            if (_t.indexOf('hxl:') > -1)
              _t = _t.replace('hxl:','')
              if (_t.indexOf('#') > -1)
                hxlTag = _t
              if (_t.indexOf('+') > -1)
                _t = _t.replace('+','')
                hxlAttrs.push(_t)
          )

        if _.isArray(hxlAttrs)
          hxlAttrsString = hxlAttrs.join(',')

        viewRowDetail.Templates.hxlTags @cid, @model.key, label, @model.get("value"), hxlTag, hxlAttrsString
      else
        viewRowDetail.Templates.hxlTags @cid, @model.key, label
    afterRender: ->
      @$el.find('input.hxlTag').select2({
          tags:$hxl.dict,
          maximumSelectionSize: 1,
          placeholder: t("#tag"),
          tokenSeparators: ['+',',', ':'],
          formatSelectionTooBig: t("Only one HXL tag allowed per question. ")
          createSearchChoice: @_hxlTagCleanup
        })
      @$el.find('input.hxlAttrs').select2({
          tags:[],
          tokenSeparators: ['+',',', ':'],
          formatNoMatches: t("Type attributes for this tag"),
          placeholder: t("Attributes"),
          createSearchChoice: @_hxlAttrCleanup
          allowClear: 1
        })

      @$el.find('input.hxlTag').on 'change', () => @_hxlUpdate()
      @$el.find('input.hxlAttrs').on 'change', () => @_hxlUpdate()

      @$el.find('input.hxlTag').on 'select2-selecting', (e) => @_hxlTagSelecting(e)
      @$el.find('.hxlTag input.select2-input').on 'keyup', (e) => @_hxlTagSanitize(e)

      @listenForInputChange({el: @$el.find('input.hxlValue').eq(0)})

    _hxlUpdate: (e)->
      tag = @$el.find('input.hxlTag').val()

      attrs = @$el.find('input.hxlAttrs').val()
      attrs = attrs.replace(/,/g, '+')
      hxlArray = [];

      if (tag)
        @$el.find('input.hxlAttrs').select2('enable', true)
        hxlArray.push('hxl:' + tag)
        if (attrs)
          aA = attrs.split('+')
          _.map(aA, (_a)->
            hxlArray.push('hxl:+' + _a)
          )
      else
        @$el.find('input.hxlAttrs').select2('enable', false)

      @model.set('value', hxlArray)
      @model.trigger('change')

    _hxlTagCleanup: (term)->
      if term.length >= 2
        regex = /\W+/g
        term = "#" + term.replace(regex, '').toLowerCase()
        return {id: term, text: term}

    _hxlTagSanitize: (e)->
      if e.target.value.length >= 2
        regex = /\W+/g
        e.target.value = "#" + e.target.value.replace(regex, '')

    _hxlTagSelecting: (e)->
      if e.val.length < 2
        e.preventDefault()

    _hxlAttrCleanup: (term)->
      regex = /\W+/g
      term = term.replace(regex, '').toLowerCase()
      return {id: term, text: term}


  viewRowDetail.DetailViewMixins.default =
    html: ->
      @fieldTab = "active"
      @$el.addClass("card__settings__fields--#{@fieldTab}")
      label = if @model.key == 'default' then t("Default response") else @model.key.replace(/_/g, ' ')
      viewRowDetail.Templates.textbox @cid, @model.key, label, 'text'
    afterRender: ->
      @$el.find('input').eq(0).val(@model.get("value"))
      @listenForInputChange()

  viewRowDetail.DetailViewMixins.calculation =
    html: -> false
    insertInDOM: (rowView)-> return

  viewRowDetail.DetailViewMixins._isRepeat =
    html: ->
      @$el.addClass("card__settings__fields--active")
      viewRowDetail.Templates.checkbox @cid, @model.key, t("Repeat"), t("Repeat this group if necessary")
    afterRender: ->
      @listenForCheckboxChange()

  viewRowDetail.DetailViewMixins.repeat_count =
    html: ->
      if @model._parent.getValue('_isRepeat')
        @$el.addClass("card__settings__fields--active")
        viewRowDetail.Templates.textbox @cid, @model.key, t("Repeat Count (number of iterations)"), 'text'
      else
        false
    afterRender: ->
      if @model._parent.getValue('_isRepeat')
        @listenForInputChange(inputType: 'text')

  # handled by mandatorySettingSelector
  viewRowDetail.DetailViewMixins.required =
    html: -> false
    insertInDOM: -> return

  viewRowDetail.DetailViewMixins.appearance =
    _extractWidth: (appearance) ->
      match = (appearance or '').match(/\bw(\d+)\b/)
      if match then parseInt(match[1]) else 4

    _stripWidth: (appearance) ->
      (appearance or '').replace(/\bw\d+\b/g, '').replace(/\s+/g, ' ').trim()

    _hasFieldList: (appearance) ->
      /\bfield-list\b/.test(appearance or '')

    _stripFieldList: (appearance) ->
      (appearance or '').replace(/\bfield-list\b/g, '').replace(/\s+/g, ' ').trim()

    _stripAll: (appearance) ->
      @_stripWidth(@_stripFieldList(appearance))

    _buildAppearanceParts: (opts) ->
      parts = []
      if opts.fieldList then parts.push('field-list')
      base = (opts.base or '').trim()
      if base then parts.push(base)
      parts.push("w#{opts.width or 4}")
      parts.join(' ')

    getTypes: () ->
      groupTypes = [['other', 'Advanced']]

      types =
        text: ['multiline', 'numbers']
        select_one: [
          'minimal',
          'autocomplete',
          'quick',
          'horizontal-compact',
          'horizontal',
          'likert',
          'compact',
          'quickcompact',
          'label',
          'list-nolabel',
        ]
        select_multiple: ['minimal', 'horizontal-compact', 'horizontal', 'compact', 'label', 'list-nolabel']
        image: ['signature', 'draw', 'annotate']
        date: ['month-year', 'year']
        group: groupTypes
        # `repeat` is a repeating group with the same appearance options
        repeat: groupTypes
        # Question Matrix is always 'field-list', regardless of provided type,
        # so we don't even allow 'other' here
        kobomatrix: []

      return types[@model._parent.getValue('type').split(' ')[0]]

    _buildWidthSelect: (selectedWidth) ->
      options = ("<option value=\"#{i}\"#{if i is selectedWidth then ' selected' else ''}>#{i}</option>" for i in [1..10]).join('')
      """<select class="appearance-width-units">#{options}</select>"""

    html: ->
      @$el.addClass("card__settings__fields--active")
      modelValue = @model.get('value') or ''
      currentWidth = @_extractWidth(modelValue)
      widthField = viewRowDetail.Templates.field(
        @_buildWidthSelect(currentWidth),
        "#{@cid}-width",
        t("Width units")
      )
      if @model_is_group(@model)
        isChecked = @_hasFieldList(modelValue)
        checkedAttr = if isChecked then ' checked' else ''
        fieldListCheckbox = viewRowDetail.Templates.field(
          """<input type="checkbox" class="appearance-field-list" id="#{@cid}-fieldlist"#{checkedAttr}/> <label for="#{@cid}-fieldlist">#{t("Show all questions in this group on the same screen")}</label>""",
          "#{@cid}-fieldlist-wrap",
          t("Display (only for pages theme)")
        )
        otherValue = @_stripAll(modelValue)
        otherField = viewRowDetail.Templates.field(
          """<input type="text" class="appearance-other-text" value="#{otherValue}" />""",
          "#{@cid}-other",
          t("Appearance (advanced)")
        )
        return otherField + widthField + fieldListCheckbox
      else
        appearances = @getTypes()
        if appearances?
          appearances.push 'other'
          appearances.unshift 'select'
          return viewRowDetail.Templates.dropdown(@cid, @model.key, appearances, t("Appearance (advanced)")) + widthField
        else
          return viewRowDetail.Templates.textbox(@cid, @model.key, t("Appearance (advanced)"), 'text') + widthField

    model_is_group: (model) ->
      model._parent.constructor.key == 'group'

    _groupUpdateModel: () ->
      $fieldListCb = @$('.appearance-field-list')
      $otherInput = @$('.appearance-other-text')
      $widthInput = @$('.appearance-width-units')
      @model.set 'value', @_buildAppearanceParts(
        fieldList: $fieldListCb.prop('checked')
        base: $otherInput.val()
        width: parseInt($widthInput.val()) or 4
      )

    afterRender: ->
      modelValue = @model.get('value') or ''
      currentWidth = @_extractWidth(modelValue)

      $widthInput = @$('.appearance-width-units')

      if @model_is_group(@model)
        # Group/repeat: checkbox + text + width
        $fieldListCb = @$('.appearance-field-list')
        $otherInput = @$('.appearance-other-text')

        $fieldListCb.on 'change', () => @_groupUpdateModel()
        $otherInput.on 'change', () => @_groupUpdateModel()
        $otherInput.on 'keyup', (evt) =>
          if evt.key is 'Enter' or evt.keyCode is 13
            $otherInput.blur()
        $widthInput.on 'change', () => @_groupUpdateModel()
      else
        # Non-group: appearance select/text + width
        $select = @$('select:not(.appearance-width-units)')
        $select.addClass('group__appearance')
        baseValue = @_stripWidth(modelValue)

        $widthInput.on 'change', () =>
          width = parseInt($widthInput.val()) or 4
          @model.set 'value', @_buildAppearanceParts(fieldList: false, base: @_stripWidth(@_stripFieldList(@model.get('value') or '')), width: width)

        if $select.length > 0
          $input = $('<input/>', {class:'text', type: 'text', width: 'auto'})
          if baseValue != ''
            appearanceTypes = @getTypes()
            hasValue = false
            if appearanceTypes
              for appearanceType in appearanceTypes
                if typeof appearanceType is 'string'
                  hasValue = baseValue == appearanceType
                else if Array.isArray(appearanceType)
                  hasValue = baseValue == appearanceType[0]

            if hasValue
              $select.val(baseValue)
            else
              $select.val('other')
              $input.val(baseValue)
              @$('.settings__input').first().append $input
              @_listenForAppearanceText($input)

          $select.change () =>
            width = parseInt($widthInput.val()) or 4
            if $select.val() == 'other'
              @model.set 'value', @_buildAppearanceParts(fieldList: false, base: '', width: width)
              @$('.settings__input').first().append $input
              @_listenForAppearanceText($input)
            else if $select.val() == 'select'
              @model.set 'value', "w#{width}"
            else
              @model.set 'value', @_buildAppearanceParts(fieldList: false, base: $select.val(), width: width)
              $input.remove()
        else
          $input = @$('input:not(.appearance-width-units)')
          if $input.attr('type') == 'text'
            $input.val(baseValue)
            @_listenForAppearanceText($input)

    _listenForAppearanceText: ($input) ->
      $widthInput = @$('.appearance-width-units')
      $input.on 'change', () =>
        width = parseInt($widthInput.val(), 10) or 4
        @model.set 'value', @_buildAppearanceParts(fieldList: false, base: $input.val(), width: width)
      $input.on 'keyup', (evt) =>
        if evt.key is 'Enter' or evt.keyCode is 13
          $input.blur()

  viewRowDetail
