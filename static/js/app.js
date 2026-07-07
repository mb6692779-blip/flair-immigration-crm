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
