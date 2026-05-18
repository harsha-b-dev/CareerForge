document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".chip-enabled").forEach(select => {
    const wrapper = document.createElement("div");
    const chipList = document.createElement("div");

    wrapper.className = "chip-select";
    chipList.className = "chip-list is-empty";

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(chipList);
    wrapper.appendChild(select);

    function updateChips() {
      chipList.textContent = "";
      const selectedOptions = Array.from(select.selectedOptions);
      chipList.classList.toggle("is-empty", selectedOptions.length === 0);

      selectedOptions.forEach(option => {
        const chip = document.createElement("div");
        const text = document.createElement("span");
        const closeButton = document.createElement("button");

        chip.className = "chip";
        text.textContent = option.textContent;
        closeButton.type = "button";
        closeButton.setAttribute("aria-label", "Remove " + option.textContent);
        closeButton.textContent = "x";

        closeButton.addEventListener("click", () => {
          option.selected = false;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        });

        chip.appendChild(text);
        chip.appendChild(closeButton);
        chipList.appendChild(chip);
      });
    }

    select.addEventListener("change", updateChips);
    updateChips();
  });
});
