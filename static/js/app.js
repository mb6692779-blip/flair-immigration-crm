function updateInactiveFields(){
  document.querySelectorAll(".client-status").forEach(sel=>{
    const form = sel.closest("form") || sel.closest("tr");
    if(!form) return;
    const reason = form.querySelector(".inactive-reason");
    if(reason){
      reason.style.display = sel.value === "Inactive" ? "block" : "none";
    }
  });
}
document.addEventListener("change", e=>{
  if(e.target.classList.contains("client-status")) updateInactiveFields();
});
window.addEventListener("load", updateInactiveFields);

function buildPartnerBoxes(){
  const type = document.getElementById("sectionType");
  const size = document.getElementById("groupSize");
  const box = document.getElementById("partnerBoxes");
  if(!type || !size || !box) return;
  let n = parseInt(size.value || "1", 10);
  box.innerHTML = "";
  if(type.value !== "Group" || n <= 1) return;
  for(let i=1; i<n; i++){
    const input = document.createElement("input");
    input.name = "partner_" + i;
    input.placeholder = "Partner Name " + i;
    input.style.marginBottom = "8px";
    box.appendChild(input);
  }
}
function toggleByValue(sel, targetId, value){
  const target = document.getElementById(targetId);
  if(target) target.style.display = sel.value === value ? "block" : "none";
}
window.addEventListener("load", buildPartnerBoxes);
